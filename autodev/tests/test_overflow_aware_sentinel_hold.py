"""
Layer 2 — orchestrator-side overflow-aware sentinel hold.

The context-overflow discarded-verdict race: an agent turn dies on
``stopReason:"error" / "Context overflow: estimated context size exceeds safe
threshold during tool loop."``; the plugin's ``agent_end`` backstop writes
``.done`` unconditionally; the poll accepts it; the gate sees no verdict and
escalates; then OpenClaw compacts-and-resumes the SAME session and writes a valid
verdict seconds-to-minutes later — too late (observed live: escalated 14:40:00,
valid PASS landed 14:43:13).

These tests cover the orchestrator helpers + the acceptor factory that close the
race by HOLDING a ``.done`` belonging to a recoverable-overflow turn until the real
verdict lands (or the session genuinely stalls / a hold budget is exhausted).

Companion to the poller-level tests in ``test_sentinel_poller_acceptor.py``.

FIND-ID: FIND-POLLING (Layer 2 — context-overflow discarded-verdict race)
"""

import inspect
import json
import os
import re
import sys
import time
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orc  # noqa: E402

OVERFLOW_MSG = (
    "Context overflow: estimated context size exceeds safe threshold during tool loop."
)


# --------------------------------------------------------------------------- helpers


def _make_orch():
    """Bare orchestrator (no __init__) for hermetic single-method tests."""
    o = orc.Orchestrator.__new__(orc.Orchestrator)
    o.lock_fd = None
    return o


def _write_session_jsonl(openclaw_root, role, session_key, last_error_message=None, clean=False):
    """Create OPENCLAW_ROOT/agents/{role}/sessions/{sessions.json,<sid>.jsonl}.

    last_error_message → final assistant row carries stopReason=error + that
    errorMessage (the overflow case). clean=True → a normal stop row (genuine end).
    Returns the synthetic sessionId.
    """
    sdir = os.path.join(openclaw_root, "agents", role, "sessions")
    os.makedirs(sdir, exist_ok=True)
    sid = "sess-" + role
    full_key = f"agent:{role}:{session_key}".lower()
    with open(os.path.join(sdir, "sessions.json"), "w") as f:
        json.dump({full_key: {"sessionId": sid}}, f)
    rows = [{"type": "message", "message": {"role": "user", "content": "go"}}]
    if last_error_message is not None:
        rows.append({"type": "message", "message": {
            "role": "assistant", "stopReason": "error", "errorMessage": last_error_message}})
    elif clean:
        rows.append({"type": "message", "message": {
            "role": "assistant", "stopReason": "stop", "content": "done"}})
    with open(os.path.join(sdir, f"{sid}.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return sid


def _acceptor(orch, role, key, attempt_start):
    return orch._make_overflow_aware_acceptor(role, key, attempt_start)


# --------------------------------------------------------------------------- T6: matcher


def test_is_recoverable_context_overflow_matches():
    """Drift guard: matches the OpenClaw overflow wording, rejects everything else."""
    assert orc._is_recoverable_context_overflow(OVERFLOW_MSG) is True
    assert orc._is_recoverable_context_overflow(
        "estimated context size exceeds safe threshold") is True
    # NOT overflow: provider/quota rejection, empties, unrelated errors
    assert orc._is_recoverable_context_overflow("402 Payment Required") is False
    assert orc._is_recoverable_context_overflow("") is False
    assert orc._is_recoverable_context_overflow(None) is False
    assert orc._is_recoverable_context_overflow("rate limit exceeded") is False


# --------------------------------------------------------------------------- T7: resolver


def test_resolve_session_jsonl_path(tmp_path):
    root = str(tmp_path)
    key = "pipeline:phase-6:CORE-E7:reviewer-attempt-1"
    sid = _write_session_jsonl(root, "reviewer", key, last_error_message=OVERFLOW_MSG)
    with patch.object(orc, "OPENCLAW_ROOT", root):
        p = orc._resolve_session_jsonl_path("reviewer", key)
        missing = orc._resolve_session_jsonl_path(
            "reviewer", "pipeline:phase-9:NOPE:reviewer-attempt-1")
    assert p == os.path.join(root, "agents", "reviewer", "sessions", f"{sid}.jsonl")
    assert os.path.exists(p)
    assert missing is None


# --------------------------------------------------------------------------- T8: verdict freshness


def test_verdict_is_fresh_and_parseable(tmp_path):
    vp = os.path.join(str(tmp_path), "reviewer_output.json")
    assert orc._verdict_is_fresh_and_parseable(vp, time.time()) is False  # absent
    with open(vp, "w") as f:
        json.dump({"behavioral_verification": {"verdict": "pass"}}, f)
    assert orc._verdict_is_fresh_and_parseable(vp, time.time() - 5) is True   # fresh+valid
    assert orc._verdict_is_fresh_and_parseable(vp, time.time() + 5) is False  # stale
    with open(vp, "w") as f:
        f.write("{bad json ::")
    assert orc._verdict_is_fresh_and_parseable(vp, time.time() - 5) is False  # malformed


# --------------------------------------------------------------------------- T9-T13: factory


def test_acceptor_holds_on_overflow_without_verdict(tmp_path):
    """T9: overflow row + no verdict yet → HOLD (False)."""
    root, art = str(tmp_path / "openclaw"), str(tmp_path / "art")
    os.makedirs(art, exist_ok=True)
    key = "pipeline:phase-6:CORE-E7:reviewer-attempt-1"
    _write_session_jsonl(root, "reviewer", key, last_error_message=OVERFLOW_MSG)
    orch = _make_orch()
    with patch.object(orc, "OPENCLAW_ROOT", root), \
         patch.object(orc, "PROJECT_ARTIFACTS_DIR", art), \
         patch.object(orc, "_write_pipeline_event", lambda *a, **k: None):
        assert _acceptor(orch, "reviewer", key, time.time())() is False


def test_acceptor_accepts_when_fresh_verdict_present(tmp_path):
    """T10 — the exact 14:43 case: overflow row BUT the resumed session already
    wrote a fresh, parseable verdict → ACCEPT (never discard a real verdict)."""
    root, art = str(tmp_path / "openclaw"), str(tmp_path / "art")
    os.makedirs(art, exist_ok=True)
    key = "pipeline:phase-6:CORE-E7:reviewer-attempt-1"
    _write_session_jsonl(root, "reviewer", key, last_error_message=OVERFLOW_MSG)
    # Realistic timing: the attempt started seconds ago (the value passed as
    # min_sentinel_mtime, captured before cleanup), and the resumed session has
    # only just written the verdict — so its mtime is comfortably >= attempt_start.
    attempt_start = time.time() - 5
    with open(os.path.join(art, "reviewer_output.json"), "w") as f:
        json.dump({"behavioral_verification": {"verdict": "pass"}}, f)
    orch = _make_orch()
    with patch.object(orc, "OPENCLAW_ROOT", root), \
         patch.object(orc, "PROJECT_ARTIFACTS_DIR", art), \
         patch.object(orc, "_write_pipeline_event", lambda *a, **k: None):
        assert _acceptor(orch, "reviewer", key, attempt_start)() is True


def test_acceptor_accepts_on_genuine_giveup_no_overflow(tmp_path):
    """T11: clean end (no overflow), no verdict → ACCEPT → gate → CONTRACT_FAILURE
    path is preserved."""
    root, art = str(tmp_path / "openclaw"), str(tmp_path / "art")
    os.makedirs(art, exist_ok=True)
    key = "pipeline:phase-6:CORE-E7:reviewer-attempt-1"
    _write_session_jsonl(root, "reviewer", key, clean=True)
    orch = _make_orch()
    with patch.object(orc, "OPENCLAW_ROOT", root), \
         patch.object(orc, "PROJECT_ARTIFACTS_DIR", art), \
         patch.object(orc, "_write_pipeline_event", lambda *a, **k: None):
        assert _acceptor(orch, "reviewer", key, time.time())() is True


def test_acceptor_accepts_after_hold_budget_exhausted(tmp_path):
    """T12: overflow + no verdict but the hold budget is spent → ACCEPT (bounded)."""
    root, art = str(tmp_path / "openclaw"), str(tmp_path / "art")
    os.makedirs(art, exist_ok=True)
    key = "pipeline:phase-6:CORE-E7:reviewer-attempt-1"
    _write_session_jsonl(root, "reviewer", key, last_error_message=OVERFLOW_MSG)
    orch = _make_orch()
    with patch.object(orc, "OPENCLAW_ROOT", root), \
         patch.object(orc, "PROJECT_ARTIFACTS_DIR", art), \
         patch.object(orc, "_write_pipeline_event", lambda *a, **k: None), \
         patch.object(orc, "_OVERFLOW_HOLD_BUDGET_SECONDS", 10):
        assert _acceptor(orch, "reviewer", key, time.time() - 100)() is True


def test_acceptor_emits_overflow_hold_event_once(tmp_path):
    """T13: while holding, the sentinel_overflow_hold event fires exactly once."""
    root, art = str(tmp_path / "openclaw"), str(tmp_path / "art")
    os.makedirs(art, exist_ok=True)
    key = "pipeline:phase-6:CORE-E7:reviewer-attempt-1"
    _write_session_jsonl(root, "reviewer", key, last_error_message=OVERFLOW_MSG)
    orch = _make_orch()
    events = []
    with patch.object(orc, "OPENCLAW_ROOT", root), \
         patch.object(orc, "PROJECT_ARTIFACTS_DIR", art), \
         patch.object(orc, "_write_pipeline_event",
                      lambda ev, phase, agent, detail: events.append((ev, agent, detail))):
        acc = _acceptor(orch, "reviewer", key, time.time())
        assert acc() is False
        assert acc() is False
        assert acc() is False
    holds = [e for e in events if e[0] == "sentinel_overflow_hold"]
    assert len(holds) == 1, f"expected exactly one hold event, got {len(holds)}"
    assert holds[0][1] == "reviewer"


# --------------------------------------------------------------------------- T14: wiring guards


def test_all_agent_poll_sites_wire_sentinel_acceptor():
    src = inspect.getsource(orc)
    n = len(re.findall(r"sentinel_acceptor=self\._make_overflow_aware_acceptor\(", src))
    assert n >= 3, f"expected >=3 wired poll sites (planner/executor/reviewer), found {n}"
    for role in ("planner", "executor", "reviewer"):
        assert re.search(rf'_make_overflow_aware_acceptor\(\s*["\']{role}["\']', src), (
            f"poll site for {role} must wire the overflow-aware acceptor"
        )


def test_poll_for_sentinel_has_sentinel_acceptor_param():
    import sentinel_poller
    sig = inspect.signature(sentinel_poller.poll_for_sentinel)
    assert "sentinel_acceptor" in sig.parameters, (
        "poll_for_sentinel must accept a sentinel_acceptor predicate"
    )
