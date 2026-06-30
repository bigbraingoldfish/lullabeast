"""Deterministic in-turn tool-loop detector (Tier 1).

The reviewer (and any agent) can spin re-running the *identical* tool call inside a
single OpenClaw turn — never writing ``.done`` — while the activity stamp stays
fresh (so stall detection is blind) and OpenClaw's own loop block misses it (it
keys on identical input AND identical *output*, defeated by jittered command
output). These tests pin the deterministic, input-only catch:

  * ``_detect_tool_loop_in_jsonl`` — scans a session JSONL for the trailing run of
    consecutive-identical ``(tool_name, args)`` calls, normalising BOTH on-disk
    shapes (llamacpp ``toolCall``/``arguments`` — the shape that bit us live — and
    Anthropic ``toolUse``/``input``), excluding legitimately-repeated poll tools;
  * ``_make_tool_loop_detector`` — the zero-arg closure poll_for_sentinel consults;
  * ``_note_tool_loop`` — stamps ``ERR_TOOL_LOOP`` + emits ``tool_loop_detected``;
  * ``_tool_loop_repeat_limit`` — per-role threshold env parse (``0`` disables).

The retry/cap/escalation itself is NOT re-tested here: a ``tool_loop`` poll result
is falsy, so it flows into each agent's existing self-failure path (already covered
by the reviewer-timeout / executor-crash suites). ``test_tool_loop_wiring.py``
proves the route.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import error_codes  # noqa: E402
import orchestrator as orch_mod_top  # noqa: E402


# --- session JSONL builders (real OpenClaw assistant tool-call row shapes) ------
def _toolcall_row(name, args, *, block_type="toolCall", args_key="arguments"):
    """One assistant row carrying a single tool call.

    block_type/args_key default to the llamacpp / openai-completions shape
    (``toolCall``/``arguments``) — the exact shape observed looping live. Pass
    ``block_type="toolUse", args_key="input"`` for the Anthropic shape.
    """
    return json.dumps({
        "type": "message",
        "message": {
            "role": "assistant",
            "content": [{"type": block_type, "name": name, args_key: args, "id": "t"}],
        },
    })


def _write_jsonl(path, *rows):
    path.write_text("\n".join(rows) + "\n")


# === _detect_tool_loop_in_jsonl =================================================
def test_detects_consecutive_identical_toolcall_arguments(tmp_path):
    """The llamacpp shape that bit us live: N identical toolCall/arguments in a row."""
    f = tmp_path / "s.jsonl"
    args = {"command": "npx playwright test --grep scaffold", "timeout": 30}
    _write_jsonl(f, *[_toolcall_row("exec", args) for _ in range(8)])
    detail = orch_mod_top._detect_tool_loop_in_jsonl(str(f), 6)
    assert detail is not None
    assert detail["tool_name"] == "exec"
    assert detail["repeat_count"] >= 6


def test_detects_consecutive_identical_tooluse_input(tmp_path):
    """Provider robustness: the Anthropic toolUse/input shape must also be caught."""
    f = tmp_path / "s.jsonl"
    args = {"command": "ls -la"}
    _write_jsonl(
        f, *[_toolcall_row("exec", args, block_type="toolUse", args_key="input") for _ in range(8)]
    )
    detail = orch_mod_top._detect_tool_loop_in_jsonl(str(f), 6)
    assert detail is not None and detail["tool_name"] == "exec"


def test_no_loop_when_args_differ(tmp_path):
    """Different args each call = real progress, not a loop."""
    f = tmp_path / "s.jsonl"
    _write_jsonl(f, *[_toolcall_row("exec", {"command": f"echo {i}"}) for i in range(8)])
    assert orch_mod_top._detect_tool_loop_in_jsonl(str(f), 6) is None


def test_no_loop_when_interspersed_different_call(tmp_path):
    """Only the *trailing consecutive* run counts: a different call breaks it.
    7 identical, then a read, then 3 identical → trailing run is 3 < limit 6."""
    f = tmp_path / "s.jsonl"
    a = {"command": "make test"}
    rows = (
        [_toolcall_row("exec", a) for _ in range(7)]
        + [_toolcall_row("read", {"path": "x"})]
        + [_toolcall_row("exec", a) for _ in range(3)]
    )
    _write_jsonl(f, *rows)
    assert orch_mod_top._detect_tool_loop_in_jsonl(str(f), 6) is None


def test_excludes_poll_tools(tmp_path):
    """Legitimately-repeated poll tools (process / command_status) are never a loop."""
    f = tmp_path / "s.jsonl"
    _write_jsonl(f, *[_toolcall_row("process", {"action": "poll", "id": "p"}) for _ in range(10)])
    assert orch_mod_top._detect_tool_loop_in_jsonl(str(f), 6) is None
    f2 = tmp_path / "s2.jsonl"
    _write_jsonl(f2, *[_toolcall_row("command_status", {"id": "c"}) for _ in range(10)])
    assert orch_mod_top._detect_tool_loop_in_jsonl(str(f2), 6) is None


def test_boundary_below_and_at_limit(tmp_path):
    """limit-1 identical → no loop; exactly limit → loop."""
    args = {"command": "true"}
    below = tmp_path / "b.jsonl"
    _write_jsonl(below, *[_toolcall_row("exec", args) for _ in range(5)])
    assert orch_mod_top._detect_tool_loop_in_jsonl(str(below), 6) is None
    at = tmp_path / "a.jsonl"
    _write_jsonl(at, *[_toolcall_row("exec", args) for _ in range(6)])
    assert orch_mod_top._detect_tool_loop_in_jsonl(str(at), 6) is not None


def test_missing_or_unresolvable_jsonl_is_failsafe(tmp_path):
    """A missing/None path must read as "no loop", never raise."""
    assert orch_mod_top._detect_tool_loop_in_jsonl(str(tmp_path / "nope.jsonl"), 6) is None
    assert orch_mod_top._detect_tool_loop_in_jsonl(None, 6) is None


def test_detects_large_arg_loop_beyond_128kb(tmp_path):
    """Limit-scaled tail: a loop of LARGE-payload calls (e.g. an executor re-issuing a
    write/apply_patch with ~40 KB args) must still be caught. The old fixed 128 KB tail
    held only ~3 such rows, so an 8-deep run read as run<limit and returned None — a
    silent false-negative for exactly the role most prone to large-payload loops. The
    tail must scale with `limit` so all `limit` rows are in view."""
    f = tmp_path / "big.jsonl"
    args = {"command": "x" * 40000}  # ~40 KB/row → 8 rows ≈ 320 KB, well past the old 128 KB tail
    _write_jsonl(f, *[_toolcall_row("write", args) for _ in range(8)])
    detail = orch_mod_top._detect_tool_loop_in_jsonl(str(f), 8)
    assert detail is not None, "large-arg loop must be detected (limit-scaled tail)"
    assert detail["tool_name"] == "write"
    assert detail["repeat_count"] >= 8


# === per-role threshold env parse ==============================================
def test_tool_loop_repeat_limit_parsing(monkeypatch):
    monkeypatch.delenv("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", raising=False)
    assert orch_mod_top._tool_loop_repeat_limit("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "8") == 8
    monkeypatch.setenv("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "not-a-number")
    assert orch_mod_top._tool_loop_repeat_limit("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "8") == 8
    monkeypatch.setenv("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "10")
    assert orch_mod_top._tool_loop_repeat_limit("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "8") == 10
    monkeypatch.setenv("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "0")  # disabled
    assert orch_mod_top._tool_loop_repeat_limit("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "8") == 0
    monkeypatch.setenv("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "1")  # a single call is never a loop
    assert orch_mod_top._tool_loop_repeat_limit("TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "8") == 2


def test_err_tool_loop_registered():
    assert error_codes.ERR_TOOL_LOOP in error_codes.ALL_ERROR_CODES


# === orchestrator instance: closure + _note_tool_loop ==========================
@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {
        "pipeline_status": "RUNNING", "current_agent": "reviewer",
        "current_phase": 2, "current_phase_raw_id": "CORE-1",
        "reviewer_retries": 0, "last_action": "test",
    }
    inst.lock_fd = None
    inst.openclaw_config = {"hooks_url": "http://x", "hooks_token": "t", "pipeline": {}}
    inst.skill_manager = MagicMock()
    inst._current_attempt_retry_class = "initial_attempt"
    inst._pending_tool_loop = None
    proj = tmp_path / "pipeline-project"
    artifacts = proj / ".autodev" / "pipeline"
    artifacts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())
    return inst, orch_mod, tmp_path


def _wire_session(tmp_path, role, session_key, sid, *rows):
    """Lay down sessions.json + {sid}.jsonl so _resolve_session_jsonl_path resolves."""
    sdir = tmp_path / "agents" / role / "sessions"
    sdir.mkdir(parents=True, exist_ok=True)
    full_key = f"agent:{role}:{session_key}".lower()
    (sdir / "sessions.json").write_text(json.dumps({full_key: {"sessionId": sid}}))
    (sdir / f"{sid}.jsonl").write_text("\n".join(rows) + "\n")


def test_make_tool_loop_detector_stashes_detail_and_returns_bool(orch):
    inst, orch_mod, tmp_path = orch
    args = {"command": "npx playwright test"}
    _wire_session(
        tmp_path, "reviewer", "pipeline:phase-2:CORE-1:reviewer-attempt-1", "sid-1",
        *[_toolcall_row("exec", args) for _ in range(9)],
    )
    detector = inst._make_tool_loop_detector(
        "reviewer", "pipeline:phase-2:CORE-1:reviewer-attempt-1", 6
    )
    assert detector() is True
    assert inst._pending_tool_loop is not None
    assert inst._pending_tool_loop["tool_name"] == "exec"


def test_make_tool_loop_detector_returns_false_when_no_loop(orch):
    inst, orch_mod, tmp_path = orch
    _wire_session(
        tmp_path, "reviewer", "pipeline:phase-2:CORE-1:reviewer-attempt-1", "sid-2",
        *[_toolcall_row("exec", {"command": f"echo {i}"}) for i in range(9)],
    )
    detector = inst._make_tool_loop_detector(
        "reviewer", "pipeline:phase-2:CORE-1:reviewer-attempt-1", 6
    )
    assert detector() is False


def test_note_tool_loop_emits_event_and_stamps_error_code(orch):
    inst, orch_mod, tmp_path = orch
    inst._pending_tool_loop = {
        "tool_name": "exec", "repeat_count": 42, "args_excerpt": "npx playwright test"
    }
    inst._note_tool_loop(agent_role="reviewer", raw_id="CORE-1")
    # event emitted
    orch_mod._write_pipeline_event.assert_called()
    ev = orch_mod._write_pipeline_event.call_args[0][0]
    assert ev == "tool_loop_detected"
    # durable attribution stamped
    ps = inst.read_phase_state()
    assert ps["last_error_code"] == error_codes.ERR_TOOL_LOOP
    assert "exec" in ps["escalation_trigger_reason"]
    # one-shot: pending detail cleared
    assert inst._pending_tool_loop is None
