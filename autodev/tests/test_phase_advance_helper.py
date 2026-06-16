"""F3 — shared phase-advance helper ``_advance_to_next_pending_phase``.

Before F3, the "resolve the next pending phase and act on it" logic lived inline
only in the reviewer-PASS branch. ``SKIP`` and ``PROCEED`` set ``current_phase=0``
but never cleared ``current_phase_raw_id`` or re-ran ``phase_resolver``, so the
planner re-ran the just-closed phase (and ``PROCEED`` on a chronically-failing
phase looped straight back to escalation — the opposite of its UI promise).

F3 extracts that block into ``Orchestrator._advance_to_next_pending_phase`` and
calls it from all three sites (phase-complete, SKIP, PROCEED) so they cannot
drift. The helper returns ``"continue"`` (caller re-enters the main loop) or
``"break"`` (caller exits it), mirroring the inline block's ``continue``/``break``.

These tests pin both the refactor structure (source-grep: the three callers
delegate; the inline resolver-outcome handling is gone from the PASS branch and
the buggy SKIP/PROCEED tails are gone) and the helper's per-outcome behaviour
(unit: PENDING starts the next phase, PIPELINE_COMPLETE/BLOCKED return the right
signal, a resolver error escalates and returns ``"continue"``).
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
# Source-grep: the refactor structure (the three sites cannot drift)
# ---------------------------------------------------------------------------


def test_advance_helper_is_defined_with_trigger_kwarg():
    """The shared helper must exist and take a keyword-only ``trigger`` (used for
    the log line / post-mortems) so all three callers route through one place."""
    assert "def _advance_to_next_pending_phase(self, *, trigger" in _ORCH_SRC, (
        "Orchestrator._advance_to_next_pending_phase(self, *, trigger: str) "
        "must be defined"
    )


def _reviewer_pass_region():
    start = _ORCH_SRC.find('if gate_result == "PASS":')
    assert start != -1, "Could not locate the reviewer-PASS branch"
    end = _ORCH_SRC.find('elif gate_result == "ROUTE_EXECUTOR":', start)
    assert end != -1, "Could not locate the end of the reviewer-PASS branch"
    return _ORCH_SRC[start:end]


def test_pass_branch_delegates_to_helper():
    """The reviewer-PASS branch must call the helper and no longer carry the
    inline resolver-outcome handling (the ``"PENDING: Phase"`` literal) — proof
    the logic moved into the helper rather than being duplicated."""
    region = _reviewer_pass_region()
    assert '_advance_to_next_pending_phase(trigger="phase_complete")' in region, (
        "the PASS branch must delegate phase advance to the shared helper"
    )
    assert '"PENDING: Phase"' not in region, (
        "the inline resolver-outcome handling must move INTO the helper — the "
        "PASS branch should no longer contain the '\"PENDING: Phase\"' literal"
    )


def test_skip_delegates_to_helper_and_drops_buggy_tail():
    """SKIP must call the helper (trigger='skip') and no longer set
    current_phase=0 + 'Manual SKIP triggered' without re-resolving — that tail
    is the F3 bug."""
    idx = _ORCH_SRC.find('elif command == "SKIP":')
    assert idx != -1
    region = _ORCH_SRC[idx:idx + 700]
    assert '_advance_to_next_pending_phase(trigger="skip")' in region, (
        "SKIP must delegate to the shared advance helper so it re-resolves"
    )
    assert "Manual SKIP triggered" not in region, (
        "the buggy SKIP tail (set current_phase=0 + transition RUNNING without "
        "re-resolving) must be removed"
    )


def test_proceed_delegates_to_helper_keeps_tag_drops_buggy_tail():
    """PROCEED must mark the roadmap [x] and git-tag (preserved), then call the
    helper (trigger='proceed'), and no longer carry the buggy
    'Manual PROCEED triggered' re-run-the-stale-phase tail."""
    idx = _ORCH_SRC.find('elif command == "PROCEED":')
    assert idx != -1
    region = _ORCH_SRC[idx:idx + 1100]
    assert 'git", "tag"' in region, "PROCEED must still git-tag the completed phase"
    assert '_advance_to_next_pending_phase(trigger="proceed")' in region, (
        "PROCEED must delegate to the shared advance helper so it re-resolves"
    )
    assert "Manual PROCEED triggered" not in region, (
        "the buggy PROCEED tail must be removed"
    )


# ---------------------------------------------------------------------------
# Behaviour: per-outcome signal + state (unit; mirrors test_orchestrator_nuclear_reset)
# ---------------------------------------------------------------------------


class _R:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _ResolverRun:
    """subprocess.run stub: simulates phase_resolver (configurable rc/stdout,
    optionally writing current_phase.json) + benign git ops."""

    def __init__(self, *, rc, stdout, artifacts_dir, current_phase_json=None, stderr=""):
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.artifacts_dir = artifacts_dir
        self.current_phase_json = current_phase_json
        self.calls = []

    def __call__(self, *args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        self.calls.append(cmd)
        if isinstance(cmd, list) and any("phase_resolver" in str(x) for x in cmd):
            if self.current_phase_json is not None:
                with open(os.path.join(self.artifacts_dir, "current_phase.json"), "w") as f:
                    json.dump(self.current_phase_json, f)
            return _R(self.rc, self.stdout, self.stderr)
        if isinstance(cmd, list) and cmd[:2] == ["git", "rev-parse"]:
            return _R(0, "base123commit\n")
        return _R(0, "")  # git checkout / tag / anything else


def _make_advance_orch(tmp_path, monkeypatch, *, run, state=None):
    """A bare Orchestrator with paths at tmp_path, the given subprocess.run stub,
    queue/run-summary helpers stubbed to record calls, and time.sleep neutered."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "QUEUE_FILE", str(tmp_path / "pipeline_queue.json"))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod.subprocess, "run", run)
    monkeypatch.setattr(orch_mod.time, "sleep", lambda *a, **k: None)
    # ``_write_run_summary`` and ``_run_completion_review`` are MODULE-LEVEL
    # functions the COMPLETE/BLOCKED arms call by bare name — neutralise them at
    # the module so they don't touch real run-summary files during the unit test.
    monkeypatch.setattr(orch_mod, "_write_run_summary", lambda *a, **k: None)
    monkeypatch.setattr(orch_mod, "_run_completion_review", lambda *a, **k: None)

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = state if state is not None else {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "reviewer",
        "pipeline_status": "RUNNING",
        "phase_base_commit": "oldbase",
    }
    orch.openclaw_config = {"hooks": {"token": "tok"}}
    orch.lock_fd = None
    # Record-only instance stubs (these ARE methods, so an instance attribute
    # shadows the class method when the helper calls self._queue_*).
    orch.calls = {"update_active": [], "park": []}
    orch._queue_update_active_entry = lambda *a, **k: orch.calls["update_active"].append(a)
    orch._queue_park_active_entry = lambda *a, **k: orch.calls["park"].append(a)
    return orch


def test_advance_pending_starts_next_phase_and_returns_continue(tmp_path, monkeypatch):
    """PENDING: the helper loads the resolver-written current_phase.json, sets the
    new phase/raw_id, zeroes the three retry counters, stamps phase_start_time,
    captures phase_base_commit (git rev-parse HEAD), and returns 'continue'.
    Catches a regression where SKIP/PROCEED fail to re-resolve (the F3 bug)."""
    run = _ResolverRun(
        rc=0, stdout="PENDING: Phase CORE-E2 identified.", artifacts_dir=str(tmp_path),
        current_phase_json={"phase_number": 2, "raw_id": "CORE-E2"},
    )
    orch = _make_advance_orch(tmp_path, monkeypatch, run=run)

    sig = orch._advance_to_next_pending_phase(trigger="skip")

    assert sig == "continue"
    assert orch.state["current_phase"] == 2
    assert orch.state["current_phase_raw_id"] == "CORE-E2"
    assert orch.state["planner_retries"] == 0
    assert orch.state["executor_retries"] == 0
    assert orch.state["reviewer_retries"] == 0
    assert "phase_start_time" in orch.state
    assert orch.state["phase_base_commit"] == "base123commit"
    assert orch.state["pipeline_status"] == "RUNNING"


def test_advance_pipeline_complete_no_queue_returns_break(tmp_path, monkeypatch):
    """PIPELINE_COMPLETE with no queue: current_agent cleared to None, the active
    entry marked COMPLETED, and 'break' returned (nothing left to advance to)."""
    run = _ResolverRun(rc=0, stdout="PIPELINE_COMPLETE", artifacts_dir=str(tmp_path))
    orch = _make_advance_orch(tmp_path, monkeypatch, run=run)
    orch._read_queue = lambda: {"queue": [], "queue_mode": "auto"}
    orch._find_active_queue_entry = lambda q: (None, None)

    sig = orch._advance_to_next_pending_phase(trigger="proceed")

    assert sig == "break"
    assert orch.state["current_agent"] is None
    assert orch.state["pipeline_status"] == "PIPELINE_COMPLETE"
    assert any(a and a[0] == "COMPLETED" for a in orch.calls["update_active"]), (
        "the completed phase must mark the active queue entry COMPLETED"
    )


def test_advance_pipeline_complete_auto_advances_reinits_and_returns_continue(tmp_path, monkeypatch):
    """PIPELINE_COMPLETE with a queued next project in auto mode: the helper
    auto-advances the queue, then RE-RUNS startup init (``_run_startup_loop``) for
    the freshly-activated project BEFORE returning 'continue' — so the new project
    resolves its real phase + ``phase_base_commit`` instead of the planner running
    at a blank phase 0. Catches the Phase-8 ``ERR_MISSING_BASE_COMMIT`` bug, where
    the in-process advance re-entered the main loop with no startup init (this is
    also why PROCEEDing the last phase of project A can roll straight into B)."""
    run = _ResolverRun(rc=0, stdout="PIPELINE_COMPLETE", artifacts_dir=str(tmp_path))
    orch = _make_advance_orch(tmp_path, monkeypatch, run=run)
    orch._read_queue = lambda: {"queue": [{"id": "q1", "completion_review": False}], "queue_mode": "auto"}
    orch._find_active_queue_entry = lambda q: (0, q["queue"][0])
    orch._select_next_queue_project = lambda **k: True
    _loop_calls = []
    orch._run_startup_loop = lambda: (_loop_calls.append(1), "enter_main_loop")[1]

    sig = orch._advance_to_next_pending_phase(trigger="phase_complete")

    assert sig == "continue"
    assert _loop_calls == [1], (
        "after auto-advancing to a new project the helper must re-run startup "
        "init (_run_startup_loop) so the new project resolves its phase + base "
        "commit — not dispatch the planner at a blank phase 0"
    )


def test_advance_complete_auto_advance_exit_run_returns_break(tmp_path, monkeypatch):
    """If the freshly-advanced project's startup returns 'exit_run' (nothing left
    to do / the 20-pass cap), the helper must translate that to 'break' (leave the
    main loop) rather than 'continue' into an agent dispatch with no resolved
    phase. Pins the exit_run→break translation at the COMPLETE advance site."""
    run = _ResolverRun(rc=0, stdout="PIPELINE_COMPLETE", artifacts_dir=str(tmp_path))
    orch = _make_advance_orch(tmp_path, monkeypatch, run=run)
    orch._read_queue = lambda: {"queue": [{"id": "q1", "completion_review": False}], "queue_mode": "auto"}
    orch._find_active_queue_entry = lambda q: (0, q["queue"][0])
    orch._select_next_queue_project = lambda **k: True
    orch._run_startup_loop = lambda: "exit_run"

    sig = orch._advance_to_next_pending_phase(trigger="phase_complete")

    assert sig == "break"


def test_advance_blocked_parks_and_returns_signal(tmp_path, monkeypatch):
    """BLOCKED (resolver rc 2): the helper parks the active entry BLOCKED and
    returns 'continue' iff the queue auto-advanced, else 'break'."""
    run = _ResolverRun(rc=2, stdout="BLOCKED: Phase CORE-E2 is blocked.", artifacts_dir=str(tmp_path))
    orch = _make_advance_orch(tmp_path, monkeypatch, run=run)
    orch._queue_after_park_maybe_advance = lambda: False

    sig = orch._advance_to_next_pending_phase(trigger="skip")

    assert sig == "break"
    assert orch.state["pipeline_status"] == "BLOCKED"
    assert any(a and a[0] == "BLOCKED" for a in orch.calls["park"]), (
        "a blocked next phase must park the active queue entry BLOCKED"
    )


def test_advance_blocked_auto_advance_runs_startup_loop(tmp_path, monkeypatch):
    """BLOCKED (resolver rc 2) WITH a queued next project: the helper parks the
    active entry BLOCKED, auto-advances, and must re-run startup init for the new
    project BEFORE returning 'continue' — the same Phase-8 parity as the COMPLETE
    arm (the BLOCKED in-process advance was a second instance of the skip-init
    bug). The no-advance case stays in test_advance_blocked_parks_and_returns_signal."""
    run = _ResolverRun(rc=2, stdout="BLOCKED: Phase CORE-E2 is blocked.", artifacts_dir=str(tmp_path))
    orch = _make_advance_orch(tmp_path, monkeypatch, run=run)
    _loop_calls = []
    orch._queue_after_park_maybe_advance = lambda: True
    orch._run_startup_loop = lambda: (_loop_calls.append(1), "enter_main_loop")[1]

    sig = orch._advance_to_next_pending_phase(trigger="skip")

    assert sig == "continue"
    assert _loop_calls == [1], (
        "a BLOCKED-then-auto-advance must re-run startup init for the newly "
        "activated project (Phase-8 parity with the COMPLETE arm)"
    )
    assert any(a and a[0] == "BLOCKED" for a in orch.calls["park"]), (
        "the blocked phase must still park the active queue entry BLOCKED"
    )


def test_advance_resolver_error_routes_to_escalation(tmp_path, monkeypatch):
    """Resolver rc 1: the helper records an honest escalation reason (carrying the
    rc and the resolver's stderr), routes ``current_agent`` to ``"escalation"``,
    transitions RUNNING, and returns ``"continue"`` so the caller re-enters the
    loop and the main-loop escalation dispatch fires (F4). Catches a regression to
    the old silent fall-through (a dead ``"break"`` leaving status RUNNING,
    current_phase=0, and no operator signal)."""
    run = _ResolverRun(
        rc=1, stdout="ERROR: roadmap not found", artifacts_dir=str(tmp_path),
        stderr="Traceback: roadmap.md not found",
    )
    orch = _make_advance_orch(tmp_path, monkeypatch, run=run)

    sig = orch._advance_to_next_pending_phase(trigger="phase_complete")

    assert sig == "continue"
    assert orch.state["current_agent"] == "escalation"
    assert orch.state["pipeline_status"] == "RUNNING"
    ps = orch.read_phase_state()
    assert ps.get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"
    reason = ps.get("escalation_trigger_reason") or ""
    assert "rc=1" in reason, "the escalation reason must surface the resolver rc"
    assert "roadmap.md not found" in reason, (
        "the escalation reason must surface the resolver's stderr so the advisory "
        "is honest about the real cause"
    )


def test_advance_resolver_unexpected_output_escalates(tmp_path, monkeypatch):
    """Resolver rc 0 but stdout matches none of PENDING / PIPELINE_COMPLETE /
    BLOCKED: the helper must treat the unrecognised verdict as a failure and
    escalate, not fall through to a blind ``"break"``. Catches the subtle case
    where the rc is fine but the resolver emitted garbage."""
    run = _ResolverRun(
        rc=0, stdout="garbage not a known token", artifacts_dir=str(tmp_path),
    )
    orch = _make_advance_orch(tmp_path, monkeypatch, run=run)

    sig = orch._advance_to_next_pending_phase(trigger="proceed")

    assert sig == "continue"
    assert orch.state["current_agent"] == "escalation"
    assert orch.state["pipeline_status"] == "RUNNING"
    assert orch.read_phase_state().get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"


def test_advance_resolver_timeout_routes_to_escalation(tmp_path, monkeypatch):
    """The resolver subprocess.run is bounded by GATE_SUBPROCESS_TIMEOUT; a hung
    resolver raises ``TimeoutExpired``. The helper's ``except`` must include
    ``TimeoutExpired`` so it is caught and routed to the SAME F4 escalation a
    resolver crash gets (result stays None → 'raised before returning a verdict'),
    returning 'continue'. Without TimeoutExpired in that except, a bounded-but-hung
    resolver would escape the helper entirely — the regression this guards."""
    from subprocess import TimeoutExpired

    def _raise_timeout(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, list) and any("phase_resolver" in str(x) for x in cmd):
            raise TimeoutExpired(cmd, kwargs.get("timeout", 60))
        return _R(0, "")

    orch = _make_advance_orch(tmp_path, monkeypatch, run=_raise_timeout)

    sig = orch._advance_to_next_pending_phase(trigger="phase_complete")

    assert sig == "continue"
    assert orch.state["current_agent"] == "escalation"
    assert orch.state["pipeline_status"] == "RUNNING"
    ps = orch.read_phase_state()
    assert ps.get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"
    assert "raised before returning a verdict" in (ps.get("escalation_trigger_reason") or ""), (
        "a timed-out resolver must escalate via the result-is-None F4 path, proving "
        "TimeoutExpired is caught by the helper's except and not propagated"
    )
