"""F9 — queue optimistic-concurrency (version-CAS) tests, orchestrator side.

TDD: written before the implementation. These pin the version-stamping contract on
``_write_queue`` and the read→apply→compare-and-swap→retry behaviour of the shared
``mutate_queue`` helper (wired into the orchestrator as ``_mutate_queue`` /
``_peek_queue_version``), including the two non-idempotent-side-effect guards on
``_select_next_queue_project``.
"""
import json
import os
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

import pytest

# Path setup (matches conftest.py / test_orchestrator_queue.py pattern)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from orchestrator import Orchestrator  # noqa: E402
from queue_semantics import (  # noqa: E402
    QUEUE_MAX_CAS_RETRIES,
    QueueAbort,
    QueueVersionConflict,
    read_queue_version,
)


def _make_entry(name, state="READY", position=1, parent_id=None, entry_id=None, project_path=None):
    return {
        "id": entry_id or str(uuid.uuid4()),
        "project_path": project_path or f"/tmp/proj_{name}",
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


def _read_raw(path):
    with open(str(path)) as f:
        return json.load(f)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    """Orchestrator instance with QUEUE_FILE/STATE_FILE pointed at tmp_path."""
    queue_file = tmp_path / "pipeline_queue.json"
    state_file = tmp_path / "pipeline_state.json"

    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import importlib
    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    from orchestrator import Orchestrator as FreshOrch

    inst = FreshOrch.__new__(FreshOrch)
    inst.state = {
        "current_phase": 0,
        "current_phase_raw_id": "",
        "current_agent": "planner",
        "pipeline_status": "RUNNING",
        "project_path": "/tmp/current_project",
    }
    inst.lock_fd = None

    monkeypatch.setattr(orch_mod, "QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(state_file))
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))

    return inst, queue_file, state_file, tmp_path


# ---------------------------------------------------------------------------
# O1/O2/O3 — version stamping + legacy tolerance
# ---------------------------------------------------------------------------

class TestQueueVersionStamping:
    def test_write_stamps_queue_version_from_zero(self, orch):
        """O1: a write to a versionless (legacy) payload lands queue_version == 1."""
        inst, queue_file, _, _ = orch
        data = {"queue": [_make_entry("a")], "queue_mode": "auto", "last_updated": ""}
        inst._write_queue(data)
        assert _read_raw(queue_file)["queue_version"] == 1

    def test_write_increments_existing_version(self, orch):
        """O2: monotonic increment — base 5 -> 6."""
        inst, queue_file, _, _ = orch
        data = {"queue": [], "queue_mode": "auto", "last_updated": "", "queue_version": 5}
        inst._write_queue(data)
        assert _read_raw(queue_file)["queue_version"] == 6

    def test_read_tolerates_legacy_file_without_version(self, orch):
        """O3: a legacy file (no queue_version) still loads; read_queue_version -> 0."""
        inst, queue_file, _, _ = orch
        _write_queue(queue_file, [_make_entry("a")])  # no queue_version key
        q = inst._read_queue()
        assert read_queue_version(q) == 0
        assert len(q["queue"]) == 1


# ---------------------------------------------------------------------------
# O4/O5 — the CAS loop itself (via _mutate_queue)
# ---------------------------------------------------------------------------

class TestMutateQueueCAS:
    def test_cas_conflict_rereads_reapplies_and_succeeds(self, orch, monkeypatch):
        """O4 (core no-lost-update): a concurrent writer adds entry Y and bumps the
        on-disk version between our read and our pre-write version check. The CAS loop
        must detect the conflict, re-read, re-apply our COMPLETED flip onto the fresh
        base, and commit — leaving BOTH Y and X=COMPLETED on disk."""
        inst, queue_file, _, _ = orch
        x = _make_entry("X", state="READY", position=1)
        _write_queue(queue_file, [x])  # version absent -> base 0

        calls = {"n": 0}

        def fake_peek():
            calls["n"] += 1
            if calls["n"] == 1:
                # Simulate the concurrent UI add landing on disk + a version bump.
                cur = _read_raw(queue_file)
                cur["queue"].append(_make_entry("Y", position=2))
                cur["queue_version"] = 1
                with open(str(queue_file), "w") as f:
                    json.dump(cur, f)
                return 1  # != base 0 -> forces a conflict
            return read_queue_version(_read_raw(queue_file))

        monkeypatch.setattr(inst, "_peek_queue_version", fake_peek)

        def mutate_fn(data):
            for e in data["queue"]:
                if e["id"] == x["id"]:
                    e["state"] = "COMPLETED"
                    return True
            raise QueueAbort()

        assert inst._mutate_queue(mutate_fn) is True

        final = _read_raw(queue_file)
        by_name = {e["name"]: e for e in final["queue"]}
        assert by_name["X"]["state"] == "COMPLETED"   # our update survived
        assert "Y" in by_name                          # the concurrent add survived
        assert final["queue_version"] == 2             # 0 -> (concurrent) 1 -> (ours) 2

    def test_cas_retry_is_bounded(self, orch, monkeypatch):
        """O5: a perpetual conflict (peek never equals base) raises QueueVersionConflict
        after exactly QUEUE_MAX_CAS_RETRIES attempts — it does not spin forever."""
        inst, queue_file, _, _ = orch
        _write_queue(queue_file, [_make_entry("X")], queue_version=0)
        monkeypatch.setattr(inst, "_peek_queue_version", lambda: 999999)

        attempts = {"n": 0}

        def mutate_fn(data):
            attempts["n"] += 1
            return True

        with pytest.raises(QueueVersionConflict):
            inst._mutate_queue(mutate_fn)
        assert attempts["n"] == QUEUE_MAX_CAS_RETRIES


# ---------------------------------------------------------------------------
# O6 — _promote_answered_escalations commits exactly once (no nested CAS)
# ---------------------------------------------------------------------------

class TestPromoteAnsweredEscalationsSingleWrite:
    def test_single_committed_write_for_one_promotion(self, orch, tmp_path, monkeypatch):
        """O6: promoting one banked ESCALATION row issues exactly one os.replace and
        flips it to ESCALATION_ANSWERED — the CAS refactor must not double-write or
        nest a second CAS round."""
        inst, queue_file, _, _ = orch
        proj = tmp_path / "parked_proj"
        (proj / ".autodev" / "pipeline").mkdir(parents=True)
        (proj / ".autodev" / "pipeline" / "pending_escalation_command.json").write_text(
            json.dumps({"command": "RESET_PHASE"})
        )
        esc = _make_entry("parked", state="ESCALATION", position=1, project_path=str(proj))
        _write_queue(queue_file, [esc], queue_version=3)

        replace_calls = []
        original_replace = os.replace

        def tracking_replace(src, dst):
            replace_calls.append(dst)
            return original_replace(src, dst)

        monkeypatch.setattr("os.replace", tracking_replace)

        changed = inst._promote_answered_escalations(inst._read_queue())
        assert changed is True
        assert sum(1 for d in replace_calls if d == str(queue_file)) == 1

        final = _read_raw(queue_file)
        assert final["queue"][0]["state"] == "ESCALATION_ANSWERED"
        assert final["queue_version"] == 4


# ---------------------------------------------------------------------------
# O7/O8 — _select_next_queue_project: side effects exactly once / graceful abort
# ---------------------------------------------------------------------------

class TestSelectNextQueueProjectCAS:
    def _ready_project(self, tmp_path, name):
        proj = tmp_path / name
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "roadmap.md").write_text("# x")
        return proj

    def test_active_commit_side_effects_run_once_under_conflict(self, orch, tmp_path, monkeypatch):
        """O8: one CAS conflict at the ACTIVE commit must NOT re-run the non-idempotent
        external side effects — update_symlink and write_state each fire exactly once."""
        inst, queue_file, _, _ = orch
        proj = self._ready_project(tmp_path, "readyproj")
        entry = _make_entry("readyproj", state="READY", position=1, project_path=str(proj))
        _write_queue(queue_file, [entry], queue_version=0)

        symlink_calls = {"n": 0}
        state_calls = {"n": 0}

        def fake_symlink(p):
            symlink_calls["n"] += 1
            return True

        def fake_write_state():
            state_calls["n"] += 1

        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", fake_symlink)
        monkeypatch.setattr(inst, "write_state", fake_write_state)
        monkeypatch.setattr(inst, "_apply_pending_escalation_command", lambda p: None)

        # Force exactly one version conflict at the commit's pre-write check.
        peeks = {"n": 0}

        def fake_peek():
            peeks["n"] += 1
            if peeks["n"] == 1:
                cur = _read_raw(queue_file)
                cur["queue_version"] = read_queue_version(cur) + 1
                with open(str(queue_file), "w") as f:
                    json.dump(cur, f)
                return cur["queue_version"]  # != base -> conflict once
            return read_queue_version(_read_raw(queue_file))

        monkeypatch.setattr(inst, "_peek_queue_version", fake_peek)

        assert inst._select_next_queue_project() is True
        assert _read_raw(queue_file)["queue"][0]["state"] == "ACTIVE"
        # The retried region is the queue commit only — external effects fire once.
        assert symlink_calls["n"] == 1
        assert state_calls["n"] == 1

    def test_aborts_without_forced_write_when_picked_entry_vanishes(self, orch, tmp_path, monkeypatch):
        """O7: if the picked entry is deleted by a concurrent writer before the ACTIVE
        commit, selection aborts gracefully (returns False, no forced ACTIVE write) and
        does not re-fire write_state."""
        inst, queue_file, _, _ = orch
        proj = self._ready_project(tmp_path, "readyproj")
        entry = _make_entry("readyproj", state="READY", position=1, project_path=str(proj))
        _write_queue(queue_file, [entry], queue_version=0)

        state_calls = {"n": 0}
        monkeypatch.setattr(inst, "_queue_preflight", lambda p: (True, "ok"))
        monkeypatch.setattr(inst, "update_symlink", lambda p: True)
        monkeypatch.setattr(inst, "write_state", lambda: state_calls.__setitem__("n", state_calls["n"] + 1))
        monkeypatch.setattr(inst, "_apply_pending_escalation_command", lambda p: None)

        # On the commit's pre-write check, a concurrent writer removes the picked entry
        # and bumps the version, so every retry re-reads an empty queue -> QueueAbort.
        def fake_peek():
            cur = _read_raw(queue_file)
            if cur["queue"]:
                cur["queue"] = []
                cur["queue_version"] = read_queue_version(cur) + 1
                with open(str(queue_file), "w") as f:
                    json.dump(cur, f)
            return read_queue_version(_read_raw(queue_file))

        monkeypatch.setattr(inst, "_peek_queue_version", fake_peek)

        assert inst._select_next_queue_project(halt_if_no_eligible=False) is False
        # No entry was force-activated, and the post-commit state write never ran.
        assert _read_raw(queue_file)["queue"] == []
        assert state_calls["n"] == 0
