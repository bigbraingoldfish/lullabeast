"""Phase 8 — Queue auto-advance startup parity.

**The bug.** Phase-0 initialization (resolve ``phase 0 → first real phase`` into
``self.state``, capture ``phase_base_commit``, check out ``phase/<raw_id>``) lives
only in ``_run_startup_planner_phase_zero_and_branch`` and was invoked **once** by
``run()`` before the main loop. An **in-process** queue advance to a new project
(``_select_next_queue_project`` writes a blank ``phase 0 / raw_id "" / planner``
state, the caller returns ``"continue"``) re-entered the *main loop* and skipped
that init entirely — so the new project's executor ran against an empty ``raw_id``
with no ``phase_base_commit`` → permanent ``ERR_MISSING_BASE_COMMIT`` → escalation
(observed live on Tick-Tac-Toe, 2026-06-06). The startup path's own
PIPELINE_COMPLETE advance already re-ran init via the ``"retry_startup"`` signal;
the three main-loop advance sites did not — the asymmetry that is the bug.

**The fix.** Extract ``run()``'s inline startup loop into ``_run_startup_loop()``
and call it after every in-process advance that activates a fresh-start project
(PIPELINE_COMPLETE advance, BLOCKED park-advance, escalation park-advance). The
method self-guards (a revival activation leaves ``current_agent="escalation"``, on
which ``_run_startup_planner_phase_zero_and_branch`` early-returns
``"enter_main_loop"`` — a no-op), so it is safe to call on both branches.

These tests pin the extracted method's loop semantics (unit) and the refactor
structure (source-grep: ``run()`` and all three advance sites delegate to it). The
per-site behavioural assertions (the helper actually *calls* ``_run_startup_loop``
after advancing) live in ``test_phase_advance_helper.py``.
"""

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
# Unit: _run_startup_loop() loop semantics
# ---------------------------------------------------------------------------


def _loop_orch(returns):
    """A bare Orchestrator whose ``_run_startup_planner_phase_zero_and_branch`` is
    stubbed to yield ``returns`` in order (the last value repeats once exhausted),
    recording its call count on ``orch.startup_calls``."""
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    seq = list(returns)
    orch.startup_calls = 0

    def _stub():
        orch.startup_calls += 1
        return seq[orch.startup_calls - 1] if orch.startup_calls <= len(seq) else seq[-1]

    orch._run_startup_planner_phase_zero_and_branch = _stub
    return orch


def test_run_startup_loop_settles_on_enter_main_loop():
    """A single ``"enter_main_loop"`` (the old loop's ``break``) settles in one
    pass and returns ``"enter_main_loop"`` — the normal fresh-launch path."""
    orch = _loop_orch(["enter_main_loop"])
    assert orch._run_startup_loop() == "enter_main_loop"
    assert orch.startup_calls == 1


def test_run_startup_loop_honours_retry_then_settles():
    """``"retry_startup"`` (the startup fn advanced an already-complete project's
    queue) re-runs the startup fn; the next ``"enter_main_loop"`` settles it. Pins
    that the queue-advance-on-complete recursion the startup fn performs is honored
    by the extracted loop exactly as the inline ``while`` did."""
    orch = _loop_orch(["retry_startup", "enter_main_loop"])
    assert orch._run_startup_loop() == "enter_main_loop"
    assert orch.startup_calls == 2


def test_run_startup_loop_propagates_exit_run():
    """``"exit_run"`` (orchestrator should stop) propagates immediately so the
    caller leaves ``run()`` / returns ``"break"`` rather than dispatching agents."""
    orch = _loop_orch(["exit_run"])
    assert orch._run_startup_loop() == "exit_run"
    assert orch.startup_calls == 1


def test_run_startup_loop_caps_at_20_iterations():
    """An unbroken stream of ``"retry_startup"`` (a pathological queue-advance loop)
    is bounded at 20 passes and returns ``"exit_run"`` instead of spinning forever —
    preserving the inline loop's ``while _startup_pass < 20`` backstop."""
    orch = _loop_orch(["retry_startup"])
    assert orch._run_startup_loop() == "exit_run"
    assert orch.startup_calls == 20


# ---------------------------------------------------------------------------
# Source-grep: the refactor structure (run() + all 3 advance sites delegate)
# ---------------------------------------------------------------------------


def test_run_startup_loop_is_defined():
    assert "def _run_startup_loop(self)" in _ORCH_SRC, (
        "Orchestrator._run_startup_loop(self) must be defined as the single "
        "canonical 'run startup to a settled verdict' routine"
    )


def _region(decl):
    """The source of the method whose ``def`` line contains ``decl``, up to the
    next class-level ``def``."""
    start = _ORCH_SRC.find(decl)
    assert start != -1, f"Could not locate {decl!r}"
    end = _ORCH_SRC.find("\n    def ", start + 1)
    return _ORCH_SRC[start:end if end != -1 else len(_ORCH_SRC)]


def test_run_delegates_to_startup_loop_and_drops_inline_loop():
    """``run()`` must call ``_run_startup_loop()`` and no longer carry the inline
    ``while _startup_pass < 20`` loop (proof the logic moved into the method rather
    than being duplicated), while that loop now lives in ``_run_startup_loop``."""
    run_region = _region("def run(self):")
    assert "self._run_startup_loop()" in run_region, "run() must call _run_startup_loop()"
    assert "while _startup_pass < 20" not in run_region, (
        "the inline startup loop must move OUT of run() and INTO _run_startup_loop"
    )
    assert "while _startup_pass < 20" in _region("def _run_startup_loop(self)"), (
        "_run_startup_loop must carry the bounded (20-pass) startup loop"
    )


def _advance_helper_region():
    start = _ORCH_SRC.find("def _advance_to_next_pending_phase(self, *, trigger")
    assert start != -1, "Could not locate _advance_to_next_pending_phase"
    end = _ORCH_SRC.find("\n    def ", start + 1)
    assert end != -1, "Could not locate the end of _advance_to_next_pending_phase"
    return _ORCH_SRC[start:end]


def test_advance_helper_reinits_on_both_in_process_advances():
    """Both in-process advance arms inside the shared advance helper — the
    PIPELINE_COMPLETE ``_select_next_queue_project`` advance and the BLOCKED
    ``_queue_after_park_maybe_advance`` advance — must call ``_run_startup_loop``
    before re-entering the main loop. Catches a regression that re-introduces the
    skip-init ``return "continue"`` for either arm."""
    region = _advance_helper_region()
    assert region.count("self._run_startup_loop()") >= 2, (
        "both the PIPELINE_COMPLETE and BLOCKED in-process advances in "
        "_advance_to_next_pending_phase must re-run startup init via "
        "_run_startup_loop before returning 'continue'"
    )


def test_every_park_advance_triggers_startup_reinit():
    """Every in-process advance via ``_queue_after_park_maybe_advance()`` must lead
    to a startup re-init for the newly-activated project — directly via
    ``_run_startup_loop()`` at the two main-loop sites (the BLOCKED arm in the
    advance helper + the escalation-park arm, site #3), or via ``"retry_startup"``
    at the startup-fn site (which the startup loop turns into a re-run). Catches a
    future park-advance site that forgets to re-initialize (the Phase-8 bug class).
    The needle matches the 3 call sites — ``(self)`` in the ``def`` doesn't match
    ``()``."""
    needle = "_queue_after_park_maybe_advance()"
    starts, pos = [], 0
    while True:
        pos = _ORCH_SRC.find(needle, pos)
        if pos == -1:
            break
        starts.append(pos)
        pos += len(needle)
    assert len(starts) >= 3, (
        f"expected the 3 known _queue_after_park_maybe_advance() call sites, found {len(starts)}"
    )
    for start in starts:
        window = _ORCH_SRC[start:start + 500]
        assert "_run_startup_loop()" in window or "retry_startup" in window, (
            "every in-process park-advance must trigger a startup re-init "
            "(_run_startup_loop directly, or 'retry_startup' in the startup fn) "
            f"within its branch — offending site at char {start}"
        )
