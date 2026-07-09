"""Run-exit sweep — no pipeline session may keep streaming after run() exits.

**The bug.** The targeted aborts (retry-start, inline stall, escalation,
reviewer_retry, verdict reap) all key off process-local tracking of the current
run. A session that outlives them — recorded before a restart, orphaned by crash
recovery, or a steer that never confirmed — keeps streaming (and spending) after
the orchestrator exits at PIPELINE_COMPLETE / STOPPED / a fatal error, with no
process left to notice.

**The fix.** ``_sweep_inflight_pipeline_sessions()`` in ``run()``'s ``finally``:
scan each phase role's sessions.json for ``pipeline:``-namespaced keys and steer
every session whose transcript shows a PROVABLY in-flight turn. Unresolvable
transcripts are left alone — a broad scan must not inject turns into sessions it
cannot read (the opposite burden of proof from the targeted aborts). Best-effort,
never raises, and runs while ``pipeline.lock`` is still held so no successor
orchestrator can be resuming these sessions concurrently.
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()

_IN_FLIGHT_ROW = json.dumps(
    {"type": "message", "message": {"role": "assistant", "stopReason": "toolUse"}}
)
_TERMINAL_ROW = json.dumps(
    {"type": "message", "message": {"role": "assistant", "stopReason": "end_turn"}}
)


def _seed_session(openclaw_root, role, bare_key, last_row, sid):
    """Write a sessions.json entry (full lowercased gateway key) + its JSONL."""
    sdir = os.path.join(openclaw_root, "agents", role, "sessions")
    os.makedirs(sdir, exist_ok=True)
    idx_path = os.path.join(sdir, "sessions.json")
    idx = {}
    if os.path.exists(idx_path):
        with open(idx_path) as f:
            idx = json.load(f)
    full_key = f"agent:{role}:{bare_key}".lower()
    idx[full_key] = {"sessionId": sid, "status": "running"}
    with open(idx_path, "w") as f:
        json.dump(idx, f)
    with open(os.path.join(sdir, f"{sid}.jsonl"), "w") as f:
        f.write(last_row + "\n")
    return full_key


def _make_orch(monkeypatch, tmp_path):
    """Bare Orchestrator over a tmp OPENCLAW_ROOT, with the steer chokepoint
    stubbed to record calls (the liveness oracle runs for real against the
    seeded transcripts)."""
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path / "art"))
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.openclaw_config = {}
    orch.state = {"current_phase_raw_id": "CORE-1"}
    interrupts = []
    orch._interrupt_agent_session = (
        lambda **kw: interrupts.append(kw) or "ok"
    )
    return orch, interrupts


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_sweep_steers_only_provably_inflight_pipeline_sessions(monkeypatch, tmp_path):
    orch, interrupts = _make_orch(monkeypatch, tmp_path)
    _seed_session(str(tmp_path), "planner",
                  "pipeline:phase-1:core-e2:planner-attempt-1", _IN_FLIGHT_ROW, "s1")
    _seed_session(str(tmp_path), "planner",
                  "pipeline:phase-2:core-e3:planner-attempt-1", _TERMINAL_ROW, "s2")
    _seed_session(str(tmp_path), "executor",
                  "pipeline:phase-1:core-e2:executor-attempt-1", _TERMINAL_ROW, "s3")

    orch._sweep_inflight_pipeline_sessions()

    assert len(interrupts) == 1, "only the in-flight session is steered"
    kw = interrupts[0]
    assert kw["role"] == "planner"
    assert kw["session_key"] == "pipeline:phase-1:core-e2:planner-attempt-1"
    assert kw["source"] == "run_exit"


def test_sweep_ignores_non_pipeline_and_unresolvable_sessions(monkeypatch, tmp_path):
    orch, interrupts = _make_orch(monkeypatch, tmp_path)
    # An ideas-namespaced session must never be swept, even in flight.
    _seed_session(str(tmp_path), "planner", "ideas:abc:session-1", _IN_FLIGHT_ROW, "s1")
    # A pipeline key whose JSONL is missing (unresolvable) is left alone: the
    # broad scan must not inject turns into sessions it cannot read.
    sdir = os.path.join(str(tmp_path), "agents", "executor", "sessions")
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "sessions.json"), "w") as f:
        json.dump({"agent:executor:pipeline:phase-1:x:executor-attempt-1": {}}, f)

    orch._sweep_inflight_pipeline_sessions()

    assert interrupts == []


def test_sweep_noop_without_sessions_and_on_corrupt_index(monkeypatch, tmp_path):
    orch, interrupts = _make_orch(monkeypatch, tmp_path)
    # No agents/ tree at all.
    orch._sweep_inflight_pipeline_sessions()
    # Corrupt / wrong-shape index files.
    for role, content in (("planner", "{not json"), ("reviewer", "[]")):
        sdir = os.path.join(str(tmp_path), "agents", role, "sessions")
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "sessions.json"), "w") as f:
            f.write(content)
    orch._sweep_inflight_pipeline_sessions()
    assert interrupts == []


def test_sweep_never_raises(monkeypatch, tmp_path):
    """A failing steer on one role must not abort the sweep (or run()'s finally)."""
    orch, _ = _make_orch(monkeypatch, tmp_path)
    _seed_session(str(tmp_path), "planner",
                  "pipeline:phase-1:a:planner-attempt-1", _IN_FLIGHT_ROW, "s1")
    _seed_session(str(tmp_path), "reviewer",
                  "pipeline:phase-1:a:reviewer-attempt-1", _IN_FLIGHT_ROW, "s2")

    def _boom(**kw):
        raise RuntimeError("steer exploded")

    orch._interrupt_agent_session = _boom
    orch._sweep_inflight_pipeline_sessions()  # must not raise


# ---------------------------------------------------------------------------
# Structural: wired into run()'s exit path
# ---------------------------------------------------------------------------


def test_helper_defined():
    assert "def _sweep_inflight_pipeline_sessions(self" in _ORCH_SRC


def test_run_finally_sweeps_before_releasing_lock():
    """The sweep must sit in run()'s finally, before release_lock — every exit
    (complete, stopped, halted, unhandled exception) passes through it, and the
    lock guarantees no successor orchestrator owns these sessions yet."""
    finally_idx = _ORCH_SRC.index("self._sweep_inflight_pipeline_sessions()")
    release_idx = _ORCH_SRC.index("self.release_lock()", finally_idx)
    assert release_idx - finally_idx < 400, (
        "sweep and release_lock must be adjacent in run()'s finally block"
    )
