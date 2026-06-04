"""F4 (SITE B) — startup phase_resolver failure routes to escalation.

``_run_startup_planner_phase_zero_and_branch`` runs ``phase_resolver`` at phase 0.
Before F4 it handled only rc 0 (PENDING / PIPELINE_COMPLETE) and rc 2 (BLOCKED):

  * an unexpected rc / output (e.g. rc 1 — roadmap not found) fell through to the
    branch-checkout block with an empty raw_id, so the planner was later invoked
    BLIND (no ``current_phase.json``);
  * a resolver subprocess crash was swallowed by ``except Exception`` with a
    "Proceeding; planner must self-orient" warning — the same blind-planner state.

F4 makes both paths escalate (set ``current_agent="escalation"`` + an honest
``escalation_trigger_reason``, transition RUNNING, return ``"enter_main_loop"`` so
the main-loop escalation dispatch fires). These tests pin that behaviour.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


class _R:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _StartupResolver:
    """subprocess.run stub for the startup resolver call. Returns a configurable
    rc/stdout/stderr for the ``phase_resolver`` invocation (or raises, to model a
    crashed subprocess); benign for any other command."""

    def __init__(self, *, rc=1, stdout="ERROR: roadmap not found", stderr="", raise_exc=None):
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.raise_exc = raise_exc

    def __call__(self, *args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, list) and any("phase_resolver" in str(x) for x in cmd):
            if self.raise_exc is not None:
                raise self.raise_exc
            return _R(self.rc, self.stdout, self.stderr)
        return _R(0, "")  # git / anything else


def _make_startup_orch(tmp_path, monkeypatch, *, run):
    """A bare Orchestrator with paths at tmp_path and the given subprocess.run
    stub, state primed for the phase-0 startup resolver block."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "QUEUE_FILE", str(tmp_path / "pipeline_queue.json"))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod.subprocess, "run", run)
    monkeypatch.setattr(orch_mod.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(orch_mod, "_write_run_summary", lambda *a, **k: None)
    monkeypatch.setattr(orch_mod, "_run_completion_review", lambda *a, **k: None)

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_agent": "planner",
        "current_phase": 0,
        "current_phase_raw_id": "",
        "pipeline_status": "RUNNING",
    }
    orch.openclaw_config = {"hooks": {"token": "tok"}}
    orch.lock_fd = None
    return orch


def test_startup_resolver_error_routes_to_escalation(tmp_path, monkeypatch):
    """rc 1 (roadmap not found / write failure / non-absolute path): the startup
    must escalate (current_agent='escalation', RUNNING, honest reason with the
    stderr) and return 'enter_main_loop', NOT proceed to a blind planner run with
    an empty raw_id. [fails today: falls through to enter_main_loop as planner]"""
    orch = _make_startup_orch(
        tmp_path, monkeypatch,
        run=_StartupResolver(rc=1, stdout="ERROR: roadmap not found", stderr="roadmap.md missing"),
    )

    sig = orch._run_startup_planner_phase_zero_and_branch()

    assert sig == "enter_main_loop"
    assert orch.state["current_agent"] == "escalation"
    assert orch.state["pipeline_status"] == "RUNNING"
    ps = orch.read_phase_state()
    assert ps.get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"
    assert "roadmap.md missing" in (ps.get("escalation_trigger_reason") or ""), (
        "the escalation reason must surface the resolver's stderr"
    )


def test_startup_resolver_crash_routes_to_escalation(tmp_path, monkeypatch):
    """The resolver subprocess itself raising (OSError / TimeoutExpired): the
    startup `except` must escalate too (locked decision B2), NOT warn-and-proceed
    into a blind planner run. [fails today: 'Proceeding; planner must
    self-orient']"""
    orch = _make_startup_orch(
        tmp_path, monkeypatch,
        run=_StartupResolver(raise_exc=OSError("git not found")),
    )

    sig = orch._run_startup_planner_phase_zero_and_branch()

    assert sig == "enter_main_loop"
    assert orch.state["current_agent"] == "escalation"
    assert orch.state["pipeline_status"] == "RUNNING"
    ps = orch.read_phase_state()
    assert ps.get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"
    assert "git not found" in (ps.get("escalation_trigger_reason") or ""), (
        "the escalation reason must surface the crash detail"
    )
