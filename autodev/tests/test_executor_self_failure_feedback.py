"""Phase 2 — gate-failure feedback parity.

The self-failure (gate-failure) executor retry now mirrors the proven
reviewer-rejection (ROUTE_EXECUTOR) path:

* `reset_execution('auto')` PRESERVES the working tree — it no longer runs
  `git reset --hard HEAD` for an ordinary gate failure, so the fresh executor
  session iterates on its prior work instead of rebuilding blind. The one
  exception is `ERR_UNACCOUNTED_DELETION`, where the hard reset is kept to
  auto-restore files MiniMax deleted under context pressure.
* `failure_context.json` is NOT cleared on the reset, so the fresh session can
  read the gate's note (each retry is a brand-new OpenClaw session with no
  memory of the prior attempt — file-based feedback is the only channel).
* `write_failure_context` tags an executor gate failure `source: "gate"` and
  adds a concise `retry_guidance` string, symmetric with the reviewer path's
  `source: "reviewer"`.

Pattern: bare Orchestrator with stubbed paths + a *capturing* subprocess mock so
we can assert which git commands ran (mirrors
``test_p0_stage_h_reset_execution_counters.py``).
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


class _R:
    returncode = 0
    stdout = ""
    stderr = ""


def _capture_subprocess(monkeypatch):
    """Install a subprocess.run mock that records the command list of every
    call and returns a success stub. Returns the list of recorded commands."""
    calls = []
    import subprocess as _sp

    def _run(*a, **k):
        calls.append(list(a[0]) if a and isinstance(a[0], (list, tuple)) else k.get("args"))
        return _R()

    monkeypatch.setattr(_sp, "run", _run)
    return calls


def _did_hard_reset(calls):
    return any(c == ["git", "reset", "--hard", "HEAD"] for c in calls if isinstance(c, list))


def _bare_orch(tmp_path, monkeypatch, phase_state):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    (tmp_path / "phase_state.json").write_text(json.dumps(phase_state))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "executor",
        "executor_retries": phase_state.get("executor_retries", 0),
        "reviewer_retries": 0,
        "planner_retries": 0,
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path),
    }
    orch.openclaw_config = {}
    orch.lock_fd = None
    orch._current_attempt_retry_class = "initial_attempt"
    return orch


def test_self_failure_preserves_worktree_no_hard_reset(tmp_path, monkeypatch):
    """A non-deletion gate failure must NOT `git reset --hard HEAD` — the
    executor's prior work is preserved so the retry makes targeted fixes
    instead of rebuilding blind. RED today (current code always hard-resets)."""
    calls = _capture_subprocess(monkeypatch)
    orch = _bare_orch(
        tmp_path, monkeypatch,
        {"executor_retries": 0, "executor_self_failure_retries": 0,
         "last_error_code": "ERR_TESTS_FAILING"},
    )
    orch.reset_execution("auto")
    assert not _did_hard_reset(calls), (
        "self-failure retry must preserve the working tree — no `git reset --hard "
        "HEAD` for a non-deletion gate failure"
    )


def test_unaccounted_deletion_still_hard_resets(tmp_path, monkeypatch):
    """`ERR_UNACCOUNTED_DELETION` keeps the hard reset (the MiniMax-deletion
    auto-restore safety net). Guards the deletion exception is honored."""
    calls = _capture_subprocess(monkeypatch)
    orch = _bare_orch(
        tmp_path, monkeypatch,
        {"executor_retries": 0, "executor_self_failure_retries": 0,
         "last_error_code": "ERR_UNACCOUNTED_DELETION"},
    )
    orch.reset_execution("auto")
    assert _did_hard_reset(calls), (
        "an unaccounted-deletion failure must still `git reset --hard HEAD` to "
        "restore files deleted under context pressure"
    )


def test_failure_context_survives_self_failure_reset(tmp_path, monkeypatch):
    """`failure_context.json` must survive the reset so the fresh executor
    session can read the gate's note. RED today (it is cleared)."""
    _capture_subprocess(monkeypatch)
    orch = _bare_orch(
        tmp_path, monkeypatch,
        {"executor_retries": 0, "executor_self_failure_retries": 0,
         "last_error_code": "ERR_TESTS_FAILING"},
    )
    fc = tmp_path / "failure_context.json"
    fc.write_text(json.dumps({"source": "gate", "gate_error_codes": ["ERR_TESTS_FAILING"]}))
    orch.reset_execution("auto")
    assert fc.exists(), (
        "failure_context.json must NOT be cleared on a self-failure reset — the "
        "fresh executor session reads it to learn what failed"
    )


def test_write_failure_context_tags_source_gate_with_guidance(tmp_path, monkeypatch):
    """An executor gate failure must be tagged `source: "gate"` with a concise
    `retry_guidance`, symmetric with the reviewer path's `source: "reviewer"`.
    RED today (no source/guidance fields)."""
    _capture_subprocess(monkeypatch)
    orch = _bare_orch(
        tmp_path, monkeypatch,
        {"executor_retries": 1, "last_error_code": "ERR_TESTS_FAILING"},
    )
    (tmp_path / "executor_output.json").write_text(json.dumps({
        "status": "complete",
        "test_results": {"all_passing": False},
        "failure_reason": "2 tests failed in tests/test_scoring.py",
        "tests_written": ["tests/test_scoring.py"],
        "file_manifest": ["src/scoring.py"],
    }))
    orch.write_failure_context("executor", attempt_number=2)
    ctx = json.loads((tmp_path / "failure_context.json").read_text())
    assert ctx.get("source") == "gate", (
        "executor gate-failure context must be tagged source:'gate' so the "
        "executor (and Scenario A) can distinguish it from a reviewer rejection"
    )
    assert ctx.get("retry_guidance"), (
        "must include a concise, high-signal retry_guidance note"
    )


# ---------------------------------------------------------------------------
# Doc-contract / default-message drift guards (Scenario A rewrite, D2, Q6)
# ---------------------------------------------------------------------------

import inspect  # noqa: E402
import re  # noqa: E402

import autodev.pipeline.webhook_client as wc_mod  # noqa: E402

_EXECUTOR_AGENTS_MD = os.path.join(REPO_ROOT, "autodev", "agents", "executor", "AGENTS.md")
_REVIEWER_AGENTS_MD = os.path.join(REPO_ROOT, "autodev", "agents", "reviewer", "AGENTS.md")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_executor_scenario_a_preserves_work():
    """Phase 2: the self-failure retry preserves the executor's work and points
    it at failure_context.json — it no longer claims a `git clean -fd` wipe or
    tells the agent to start blind. RED until executor/AGENTS.md Scenario A is
    rewritten (dual-source)."""
    text = _read(_EXECUTOR_AGENTS_MD)
    assert "git clean -fd" not in text, (
        "executor/AGENTS.md must not claim the workspace was `git clean -fd`'d on a "
        "self-failure retry — the orchestrator preserves the working tree (Phase 2)"
    )
    assert "None of your previous work exists" not in text, (
        "the blind-restart instruction must be removed — work is preserved on a self-failure retry"
    )
    assert "preserved on the branch" in text, (
        "Scenario A must state the executor's prior work is preserved on the branch"
    )


def test_reviewer_blocking_issues_require_scope_specificity():
    """Phase 2 (D2): the reviewer→executor handoff must name the exact scope —
    file + line/area + variable/function — so the executor knows precisely where
    to focus. RED until reviewer/AGENTS.md is strengthened (dual-source)."""
    text = _read(_REVIEWER_AGENTS_MD)
    assert "line or area" in text, (
        "reviewer/AGENTS.md blocking_issues guidance must require naming the line "
        "or area of the file so the executor knows exactly where to focus (D2)"
    )


@pytest.mark.parametrize("agent,out_json,done", [
    ("planner", "planner_output.json", "planner_output.done"),
    ("executor", "executor_output.json", "executor_output.done"),
    ("reviewer", "reviewer_output.json", "reviewer_output.done"),
])
def test_default_messages_carry_output_contract_reminder(agent, out_json, done):
    """Q6 regression lock: each pipeline agent's default webhook message must
    remind the agent to produce its output JSON and write its sentinel, so the
    output-contract reminder is injected consistently on every invocation
    (already green — locks the audited consistency against future drift)."""
    src = inspect.getsource(wc_mod.invoke_agent_webhook)
    match = re.search(rf'"{agent}"\s*:\s*\((.*?)\)\s*,', src, re.DOTALL)
    assert match, f"Could not locate {agent} default message"
    msg = match.group(1)
    assert out_json in msg and done in msg, (
        f"{agent} default webhook message must remind the agent to produce "
        f"{out_json} and write {done} (output-contract reminder, Q6)"
    )
