"""P1 Stage G2 — nuclear reset (operator escape hatch, cap 2).

``nuclear_reset_phase()`` is a thin wrapper over ``reset_phase()`` that adds its
own governance: a ``nuclear_resets`` counter (cap 2) independent of the
``escalation_resets`` cap (3), plus a ``reset_log`` audit entry. It is reached
only past the escalation cap, so it must survive the very reset it triggers —
hence ``reset_phase()`` is extended to preserve ``nuclear_resets`` and
``reset_log`` alongside ``escalation_resets``.

These tests pin:
  1. nuclear_reset_phase delegates to reset_phase's destructive mechanics + replans
  2. it increments nuclear_resets, NOT escalation_resets (the two caps are independent)
  3. nuclear_resets survives reset_phase (the coupling that makes the cap accumulate)
  4. reset_log survives reset_phase (Decision B — also fixes the latent RESET_PHASE wipe)
  5. nuclear_reset_phase appends a NUCLEAR_RESET reset_log entry
  6. a UI-banked NUCLEAR_RESET rides the Stage-H promote -> apply path command-agnostically

Harness mirrors test_p0_stage_h_phase_state_defaults.py (reset_phase drive) and
test_orchestrator_queue.py (queue/revival helpers).
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


class _Run:
    """Recording stand-in for subprocess.run that always 'succeeds'."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args[0] if args else kwargs.get("args"))

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()


def _make_orch(tmp_path, monkeypatch, *, state=None, phase_state=None):
    """A bare Orchestrator with project/queue/state paths pointed at tmp_path.

    Stubs subprocess.run (records calls) so git ops + the phase_resolver re-run
    inside reset_phase are inert. Returns (orch, orch_mod, ps_file, run_recorder).
    """
    import orchestrator as orch_mod

    ps_file = tmp_path / "phase_state.json"
    if phase_state is not None:
        ps_file.write_text(json.dumps(phase_state))

    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(ps_file))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "QUEUE_FILE", str(tmp_path / "pipeline_queue.json"))

    run = _Run()
    monkeypatch.setattr(orch_mod.subprocess, "run", run)

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = state if state is not None else {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "escalation",
        "pipeline_status": "RUNNING",
        "phase_base_commit": "abc123base",
        "last_action": "",
        "last_action_timestamp": "",
    }
    orch.openclaw_config = {"hooks": {"token": "tok"}}
    orch.lock_fd = None
    return orch, orch_mod, ps_file, run


# ---------------------------------------------------------------------------
# 1. nuclear_reset_phase reuses reset_phase mechanics + replans from planner
# ---------------------------------------------------------------------------

def test_nuclear_reset_wipes_branch_and_replans(tmp_path, monkeypatch):
    """nuclear_reset_phase must do everything reset_phase does — git reset --hard
    to the phase base commit, delete the phase branch, wipe outputs, zero retry
    counters, clear prior_blame_attributions, replan from the planner — and bump
    nuclear_resets to 1. Catches a wrapper that forgets to delegate the destructive
    mechanics."""
    phase_state = {
        "executor_retries": 3,
        "executor_self_failure_retries": 5,
        "executor_reviewer_rejection_retries": 2,
        "reviewer_retries": 1,
        "reviewer_unverified_retries": 1,
        "prior_blame_attributions": ["impl", "impl", "impl"],
        "escalation_resets": 3,
        "nuclear_resets": 0,
        "last_error_code": "ERR_INFRA_FAILURE",
    }
    orch, _mod, ps_file, run = _make_orch(tmp_path, monkeypatch, phase_state=phase_state)
    # An output file that must be wiped by the reset.
    (tmp_path / "executor_output.json").write_text("{}")

    orch.nuclear_reset_phase()

    # Destructive git mechanics (inherited from reset_phase).
    cmds = [c for c in run.calls if isinstance(c, list)]
    assert ["git", "reset", "--hard", "abc123base"] in cmds, (
        "nuclear_reset_phase must git reset --hard to the phase base commit"
    )
    assert any(c[:3] == ["git", "branch", "-D"] for c in cmds), (
        "nuclear_reset_phase must delete the phase branch"
    )
    # Outputs wiped.
    assert not (tmp_path / "executor_output.json").exists()
    # Replans from the planner with a fresh attempt class.
    assert orch.state["current_agent"] == "planner"
    assert orch._current_attempt_retry_class == "initial_attempt"

    ps = json.loads(ps_file.read_text())
    for counter in (
        "executor_retries", "executor_self_failure_retries",
        "executor_reviewer_rejection_retries", "reviewer_retries",
    ):
        assert ps.get(counter, 0) == 0, f"{counter} must be zeroed by the reset"
    # Cleared by wholesale dict replacement (key absent => empty).
    assert ps.get("prior_blame_attributions", []) == []
    assert ps.get("reviewer_unverified_retries", 0) == 0
    # Governance: nuclear budget consumed once.
    assert ps.get("nuclear_resets") == 1


# ---------------------------------------------------------------------------
# 2. The two caps are independent
# ---------------------------------------------------------------------------

def test_nuclear_reset_increments_nuclear_resets_not_escalation_resets(tmp_path, monkeypatch):
    """nuclear_resets += 1; escalation_resets untouched. The whole point of the
    feature is that it is governed by a SEPARATE cap, available precisely because
    the escalation cap (3) is spent."""
    orch, _mod, ps_file, _run = _make_orch(
        tmp_path, monkeypatch,
        phase_state={"escalation_resets": 3, "nuclear_resets": 0, "last_error_code": "X"},
    )

    orch.nuclear_reset_phase()

    ps = json.loads(ps_file.read_text())
    assert ps.get("nuclear_resets") == 1, "nuclear_resets must increment"
    assert ps.get("escalation_resets") == 3, (
        "escalation_resets must be preserved untouched — the caps are independent"
    )


# ---------------------------------------------------------------------------
# 3 + 4. reset_phase preserves the governance counter + audit log (Decision B)
# ---------------------------------------------------------------------------

def test_nuclear_resets_survives_reset_phase(tmp_path, monkeypatch):
    """reset_phase must carry nuclear_resets across its phase_state re-init. Without
    this, the cap can never accumulate (nuclear_reset_phase increments, reset_phase
    wipes) and nuclear resets would be unbounded. This is the load-bearing coupling."""
    orch, _mod, ps_file, _run = _make_orch(
        tmp_path, monkeypatch,
        phase_state={"nuclear_resets": 1, "escalation_resets": 3},
    )

    orch.reset_phase()

    ps = json.loads(ps_file.read_text())
    assert ps.get("nuclear_resets") == 1, (
        "reset_phase must preserve nuclear_resets (like escalation_resets) so the "
        "cap accumulates across the resets it governs"
    )


def test_reset_log_survives_reset_phase(tmp_path, monkeypatch):
    """reset_phase must preserve reset_log (Decision B). Today the RESET_PHASE dispatch
    appends a reset_log entry then reset_phase silently wipes it (fresh dict) — a latent
    audit-loss bug. Preserving it makes the nuclear audit entry (and the RESET_PHASE one)
    survive."""
    seed_entry = {
        "reset_number": 1, "command": "RESET_PHASE",
        "reason": "ERR_X", "timestamp": "2026-01-01T00:00:00+00:00",
    }
    orch, _mod, ps_file, _run = _make_orch(
        tmp_path, monkeypatch,
        phase_state={"reset_log": [seed_entry], "escalation_resets": 1},
    )

    orch.reset_phase()

    ps = json.loads(ps_file.read_text())
    assert ps.get("reset_log") == [seed_entry], (
        "reset_phase must preserve the reset_log audit trail across its re-init"
    )


# ---------------------------------------------------------------------------
# 5. nuclear_reset_phase logs its own audit entry
# ---------------------------------------------------------------------------

def test_nuclear_reset_appends_reset_log_entry(tmp_path, monkeypatch):
    """Each nuclear reset records {reset_number, command, reason, timestamp} so the
    post-mortem trail shows every use. reason is sourced from last_error_code."""
    orch, _mod, ps_file, _run = _make_orch(
        tmp_path, monkeypatch,
        phase_state={"nuclear_resets": 0, "escalation_resets": 3, "last_error_code": "ERR_TESTS_FAILING"},
    )

    orch.nuclear_reset_phase()

    ps = json.loads(ps_file.read_text())
    log = ps.get("reset_log", [])
    nuke_entries = [e for e in log if e.get("command") == "NUCLEAR_RESET"]
    assert len(nuke_entries) == 1, "exactly one NUCLEAR_RESET reset_log entry expected"
    entry = nuke_entries[0]
    assert entry.get("reset_number") == 1
    assert entry.get("reason") == "ERR_TESTS_FAILING"
    assert "timestamp" in entry


# ---------------------------------------------------------------------------
# 6. Stage-H forward-link: a banked NUCLEAR_RESET revives command-agnostically
# ---------------------------------------------------------------------------

def test_banked_nuclear_reset_revives_correctly(tmp_path, monkeypatch):
    """The Stage-H bank->promote->apply path is command-agnostic, so NUCLEAR_RESET
    banked for a parked project (a) promotes ESCALATION -> ESCALATION_ANSWERED and
    (b) is written through to escalation_output.json for the dispatch loop to consume
    against the revived phase. No Stage-H code change is needed; this pins that."""
    import orchestrator as orch_mod
    from queue_semantics import ESCALATION_ANSWERED

    # A parked project with a banked NUCLEAR_RESET answer.
    proj = tmp_path / "projA"
    art = proj / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    (art / "pending_escalation_command.json").write_text(
        json.dumps({"command": "NUCLEAR_RESET", "source": "ui"})
    )

    monkeypatch.setattr(orch_mod, "QUEUE_FILE", str(tmp_path / "pipeline_queue.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {"current_agent": "escalation", "pipeline_status": "QUEUE_HALTED"}
    orch.lock_fd = None
    orch.openclaw_config = {"hooks": {"token": "tok"}}
    orch.write_state = lambda: None  # assert in-memory state + files, not the on-disk write

    # (a) promotion is keyed on the pending file's presence, not the command value.
    queue_data = {"queue": [{
        "id": "q1", "name": "A", "position": 0,
        "project_path": str(proj), "state": "ESCALATION",
    }], "queue_mode": "auto"}
    changed = orch._promote_answered_escalations(queue_data)
    assert changed is True
    assert queue_data["queue"][0]["state"] == ESCALATION_ANSWERED, (
        "a banked NUCLEAR_RESET must promote the parked entry to ESCALATION_ANSWERED"
    )

    # (b) the banked command is written through verbatim for the dispatch loop.
    orch._apply_pending_escalation_command(str(proj))
    esc_json = art / "escalation_output.json"
    esc_done = art / "escalation_output.done"
    assert esc_json.exists() and esc_done.exists()
    assert json.loads(esc_json.read_text()).get("command") == "NUCLEAR_RESET"
    assert not (art / "pending_escalation_command.json").exists(), "pending file consumed"
    assert orch.state["current_agent"] == "escalation"
    assert orch.state["pipeline_status"] == "WAITING_FOR_HUMAN"


# ---------------------------------------------------------------------------
# 7 + 8. Cap accumulation + dispatch-guard at the cap (B8 — the second-cycle
#        behaviour a live UI smoke could not confirm after the backend died
#        mid-run; pinned here so it never depends on a flaky live env again).
# ---------------------------------------------------------------------------

def test_two_nuclear_resets_accumulate_to_cap(tmp_path, monkeypatch):
    """Two sequential nuclear resets accumulate `nuclear_resets` 0 -> 1 -> 2 (the cap), append
    a distinct `reset_log` entry each (reset_number 1 then 2), and leave `escalation_resets`
    untouched. This is the precondition for the cap's terminal behaviour: once at 2 the UI hides
    the button (UI static-lint test) and a third is refused (server 409 + the dispatch guard
    below). Catches a preservation regression that would silently reset the count to 1 each time.
    """
    orch, _mod, ps_file, _run = _make_orch(
        tmp_path, monkeypatch,
        phase_state={"escalation_resets": 3, "nuclear_resets": 0, "last_error_code": "ERR_X"},
    )

    orch.nuclear_reset_phase()
    orch.nuclear_reset_phase()

    ps = json.loads(ps_file.read_text())
    assert ps.get("nuclear_resets") == 2, "two resets must accumulate to the cap of 2"
    assert ps.get("escalation_resets") == 3, "escalation_resets stays untouched across both"
    nuke = [e for e in ps.get("reset_log", []) if e.get("command") == "NUCLEAR_RESET"]
    assert [e.get("reset_number") for e in nuke] == [1, 2], (
        "each nuclear reset appends its own audit entry carrying the running count"
    )


def test_dispatch_nuclear_branch_enforces_cap_in_source():
    """Real-source guard (mirrors test_p0_stage_h_phase_state_defaults' source-inspection
    style): the actual `NUCLEAR_RESET` dispatch branch in orchestrator.py must — in order —
    gate on `nuclear_resets >= 2`, send the Signal cap-reached notice in that branch, and call
    `nuclear_reset_phase()` only in the `else`. This pins the orchestrator-side cap that the
    live smoke (B8) could not confirm after the backend died, and that the banked-revival path
    reaches directly (bypassing the server's bank-time 409). A behavioural replica would only
    test a copy of the logic; asserting against the source pins the shipped branch itself.
    """
    import pathlib

    import orchestrator as orch_mod

    src = pathlib.Path(orch_mod.__file__).read_text()
    idx = src.find('command == "NUCLEAR_RESET"')
    assert idx != -1, "NUCLEAR_RESET dispatch branch not found in orchestrator.py"
    # Window must clear the in-branch comment block and reach the else-arm call.
    branch = src[idx:idx + 1200]

    i_cap = branch.find('nuclear_resets", 0) >= 2')
    i_notice = branch.find("Nuclear reset cap reached")
    i_else = branch.find("else:")
    i_reset = branch.find("self.nuclear_reset_phase()")
    assert -1 < i_cap < i_notice < i_else < i_reset, (
        "the dispatch branch must gate on nuclear_resets >= 2, send the cap-reached notice in "
        "that branch, and call nuclear_reset_phase() only in the else (order-checked)"
    )
