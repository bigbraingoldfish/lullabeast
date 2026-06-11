"""UI REVIEW Phase 4 (finding 3-A) — ``run_started_at`` is a RUN-scoped
``pipeline_state.json`` field.

It is stamped once when a fresh run begins, survives every phase advance, and is
PRESERVED (not re-stamped) across a park→revival of the same run. The badge it
backs ("(from previous run)") compares the completion report's mtime against the
run start; if ``run_started_at`` were reset per phase, or lost on revival, a
legitimately-stale report would be mislabelled.

Strategy (mirrors the repo's existing pipeline tests):
- BEHAVIOURAL test for the survives-phase-advance property — the one a source scan
  cannot prove — driving the real ``_advance_to_next_pending_phase`` with a stubbed
  resolver/git (same harness shape as ``test_phase_advance_helper.py``).
- SOURCE-WIRING tests for the fresh-start stamp, the park snapshot, and the revival
  restore, since ``_select_next_queue_project`` / ``_queue_park_active_entry`` are
  heavy to drive end-to-end (cf. ``test_w2a_run_manifest.py``).
"""
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


# ---------------------------------------------------------------------------
# Behaviour: run_started_at survives a phase advance (self-contained harness)
# ---------------------------------------------------------------------------


class _R:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _ResolverRun:
    """subprocess.run stub: simulates phase_resolver writing current_phase.json on a
    PENDING verdict, plus benign git ops."""

    def __init__(self, *, rc, stdout, artifacts_dir, current_phase_json=None):
        self.rc = rc
        self.stdout = stdout
        self.artifacts_dir = artifacts_dir
        self.current_phase_json = current_phase_json

    def __call__(self, *args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, list) and any("phase_resolver" in str(x) for x in cmd):
            if self.current_phase_json is not None:
                with open(os.path.join(self.artifacts_dir, "current_phase.json"), "w") as f:
                    json.dump(self.current_phase_json, f)
            return _R(self.rc, self.stdout)
        if isinstance(cmd, list) and cmd[:2] == ["git", "rev-parse"]:
            return _R(0, "base123commit\n")
        return _R(0, "")  # git checkout / tag / anything else


def _build_orch(tmp_path, monkeypatch, *, run, state):
    """A bare Orchestrator with module path-globals pointed at tmp_path, the given
    subprocess.run stub, and queue helpers neutralised to record-only."""
    for attr, val in {
        "PROJECT_ARTIFACTS_DIR": str(tmp_path),
        "SYMLINK_TARGET": str(tmp_path),
        "PHASE_STATE_FILE": str(tmp_path / "phase_state.json"),
        "STATE_FILE": str(tmp_path / "pipeline_state.json"),
        "QUEUE_FILE": str(tmp_path / "pipeline_queue.json"),
        "AUTODEV_PIPELINE_ROOT": str(tmp_path),
    }.items():
        monkeypatch.setattr(orch_mod, attr, val)
    monkeypatch.setattr(orch_mod.subprocess, "run", run)
    monkeypatch.setattr(orch_mod.time, "sleep", lambda *a, **k: None)

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = dict(state)
    orch.openclaw_config = {"hooks": {"token": "tok"}}
    orch.lock_fd = None
    orch.calls = {"update_active": [], "park": []}
    orch._queue_update_active_entry = lambda *a, **k: orch.calls["update_active"].append(a)
    orch._queue_park_active_entry = lambda *a, **k: orch.calls["park"].append(a)
    return orch


def test_run_started_at_survives_phase_advance(tmp_path, monkeypatch):
    """A PENDING phase advance mutates current_phase / retries / phase_start_time IN
    PLACE; a run-scoped ``run_started_at`` already in state must be untouched.
    Catches a regression that rebuilds state per phase, or wrongly re-stamps
    ``run_started_at`` on advance (which would reset the staleness baseline every
    phase and hide a stale completion report)."""
    run = _ResolverRun(
        rc=0, stdout="PENDING: Phase CORE-E2 identified.", artifacts_dir=str(tmp_path),
        current_phase_json={"phase_number": 2, "raw_id": "CORE-E2"},
    )
    orch = _build_orch(tmp_path, monkeypatch, run=run, state={
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "reviewer",
        "pipeline_status": "RUNNING",
        "phase_base_commit": "oldbase",
        "run_started_at": "2026-06-08T00:00:00+00:00",
    })

    sig = orch._advance_to_next_pending_phase(trigger="phase_complete")

    assert sig == "continue"
    assert orch.state["current_phase"] == 2  # advanced
    assert orch.state["run_started_at"] == "2026-06-08T00:00:00+00:00"  # untouched


# ---------------------------------------------------------------------------
# Source-wiring: fresh-start stamp, park snapshot, revival restore
# ---------------------------------------------------------------------------


def _region(anchor, size):
    i = _ORCH_SRC.find(anchor)
    assert i != -1, f"anchor {anchor!r} not found in orchestrator.py"
    return _ORCH_SRC[i:i + size]


def test_constructor_default_has_run_started_at():
    """The __init__ in-memory baseline carries ``run_started_at`` so the field
    always exists, even before the first fresh-run write."""
    assert "run_started_at" in _region('"last_action": "initialized",', 220)


def test_queue_fresh_start_stamps_run_started_at():
    """The non-revival (fresh-start) state dict in ``_select_next_queue_project``
    stamps ``run_started_at`` — so a queue auto-advance run has a start marker."""
    assert "run_started_at" in _region("queue auto-advance to", 400)


def test_cli_project_switch_stamps_run_started_at():
    """``apply_cli_project_path``'s fresh-start dict (the project_switch branch)
    stamps ``run_started_at``."""
    assert "run_started_at" in _region("Resetting pipeline_state.json for new project", 700)


def test_park_snapshot_includes_run_started_at():
    """``_queue_park_active_entry`` snapshots ``run_started_at`` so a later revival
    restores the ORIGINAL run start (a parked project is the same run)."""
    # Window spans the whole snapshot dict (~10 fields + inline comments) through
    # its closing brace — run_started_at sits ~1.1k chars in, brace at ~1.35k.
    assert "run_started_at" in _region("snapshot = {", 1600)


def test_revival_restores_run_started_at():
    """Both revival branches (answered-revival and escalation bring-up) restore
    ``run_started_at`` from the parked snapshot rather than leaving it unset — which
    would blank the staleness baseline on revive. Asserted by occurrence count."""
    occurrences = _ORCH_SRC.count('self.state["run_started_at"] = snap["run_started_at"]')
    assert occurrences >= 2, (
        "both revival branches must restore run_started_at from the parked snapshot"
    )


# ---------------------------------------------------------------------------
# apply_cli_project_path — the --project-path (queue trigger-next) launch path.
# Behavioural: the SAME-project else branch must stamp on a fresh re-start but
# preserve on a crash-resume. This is the gap live validation caught — trigger-next
# spawns `--project-path`, and re-running a finished project hit the else branch,
# which loaded disk state and never stamped run_started_at (badge stayed dead).
# ---------------------------------------------------------------------------


def _make_cli_orch(tmp_path, monkeypatch, disk_state):
    """Bare orchestrator with STATE_FILE seeded to ``disk_state``; update_symlink and
    write_state stubbed so ``apply_cli_project_path`` can be driven hermetically."""
    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps(disk_state))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(state_file))
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {}
    orch.update_symlink = lambda t: True
    orch.write_state = lambda: None
    return orch


def test_cli_project_path_same_project_fresh_run_stamps(tmp_path, monkeypatch):
    """A --project-path (re)start on the SAME project from a terminal/idle disk state —
    the queue trigger-next path re-running a finished project — is a NEW run and MUST
    stamp run_started_at. This is the gap that left queue-launched runs with a null
    run_started_at and a dead staleness badge (caught in live validation)."""
    proj = str(tmp_path / "proj")
    os.makedirs(proj, exist_ok=True)
    orch = _make_cli_orch(tmp_path, monkeypatch, {
        "project_path": proj, "pipeline_status": "IDLE",
        "current_phase": 0, "current_agent": "planner",
        # no run_started_at on disk (prior run never stamped / fresh prime)
    })

    orch_mod.apply_cli_project_path(orch, proj)

    assert orch.state.get("run_started_at"), (
        "same-project fresh re-run (trigger-next) must stamp run_started_at"
    )
    import datetime as _dt
    _dt.datetime.fromisoformat(orch.state["run_started_at"])  # ISO8601, parseable


def test_cli_project_path_resume_preserves_run_started_at(tmp_path, monkeypatch):
    """A --project-path restart while the SAME project is mid-run (WAITING/RUNNING) is a
    crash-resume of the same run — run_started_at MUST be PRESERVED, not re-stamped, else
    a completion report written before the crash would be mislabelled '(from previous
    run)'."""
    proj = str(tmp_path / "proj")
    os.makedirs(proj, exist_ok=True)
    original = "2026-06-01T00:00:00+00:00"
    orch = _make_cli_orch(tmp_path, monkeypatch, {
        "project_path": proj, "pipeline_status": "WAITING_FOR_SENTINEL",
        "current_phase": 2, "current_agent": "executor",
        "run_started_at": original,
    })

    orch_mod.apply_cli_project_path(orch, proj)

    assert orch.state.get("run_started_at") == original, (
        "an active-run resume must preserve the original run_started_at"
    )
