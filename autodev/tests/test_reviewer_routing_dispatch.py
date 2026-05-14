"""Section 5 — reviewer-gate verdict routing audit.

The reviewer gate (``autodev/pipeline/gate_scripts/reviewer_gate.py``) can
emit seven distinct verdicts: ``PASS``, ``ROUTE_EXECUTOR``,
``ROUTE_PLANNER``, ``ROUTE_ESCALATE``, ``MISSING_ARTIFACTS``,
``INFRA_FAILURE``, and ``VISUAL_UNVERIFIED``.  The orchestrator's
``if/elif`` chain had explicit handlers for the first six and silently
fell through on the seventh — ``current_agent`` stayed ``"reviewer"`` and
the next loop iteration re-invoked the reviewer in a new session,
exactly the symptom observed live for CORE-E6 (3 reviewer invocations,
no executor between them).

These tests pin:

* Every verdict the gate can emit is handled explicitly (no fall-through).
* An unknown verdict transitions to ``HALTED_SILENT`` rather than loop.
* ``ROUTE_EXECUTOR`` writes the reviewer's blocking-issue payload to
  ``failure_context.json`` so the next executor pass receives the
  context it needs to fix the issue.
* ``reset_execution`` keeps ``reviewer_retries`` synchronised between
  ``phase_state.json`` and ``pipeline_state.json``.
* A diagnostic ``[REVIEWER_GATE]`` log line is emitted on every gate
  consumption so operators can reconstruct routing decisions from logs.
"""

import json
import os
import re
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
_GATE_SRC = open(
    os.path.join(PIPELINE_DIR, "gate_scripts", "reviewer_gate.py"),
    encoding="utf-8",
).read()


# The canonical, complete set of verdicts the gate can emit.  If anyone
# adds a new verdict to reviewer_gate.py this test surface forces them
# to add an explicit handler too.
KNOWN_VERDICTS = (
    "PASS",
    "ROUTE_EXECUTOR",
    "ROUTE_PLANNER",
    "ROUTE_ESCALATE",
    "MISSING_ARTIFACTS",
    "INFRA_FAILURE",
    "VISUAL_UNVERIFIED",
)


# ---------------------------------------------------------------------------
# R1–R7: explicit handler for every existing verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", KNOWN_VERDICTS)
def test_orchestrator_has_explicit_handler_for_verdict(verdict):
    """Every verdict the gate can return must be matched by an explicit
    handler in orchestrator.py.  A missing handler is the silent
    fall-through bug class that caused the CORE-E6 reviewer loop."""
    # Look for either ``== "VERDICT"`` (if/elif chain) or ``"VERDICT":``
    # (dispatch-table refactor — also acceptable).
    pat = re.compile(
        r'(==\s*["\']' + verdict + r'["\']|["\']'
        + verdict + r'["\']\s*:)',
    )
    assert pat.search(_ORCH_SRC), (
        f"orchestrator must have an explicit handler for reviewer verdict "
        f"{verdict!r} — silent fall-through is the regression that produced "
        f"the CORE-E6 reviewer→reviewer loop"
    )


# ---------------------------------------------------------------------------
# R8: VISUAL_UNVERIFIED — new handler
# ---------------------------------------------------------------------------


def test_visual_unverified_referenced_in_orchestrator():
    """VISUAL_UNVERIFIED must appear in orchestrator.py (not just in the
    gate script) so it cannot fall through silently."""
    assert "VISUAL_UNVERIFIED" in _ORCH_SRC, (
        "orchestrator must reference VISUAL_UNVERIFIED — without an "
        "explicit handler, current_agent stays 'reviewer' and the next "
        "loop re-invokes the reviewer in a new session"
    )


def test_visual_unverified_emitted_by_gate_script():
    """Pre-condition: the gate script still emits VISUAL_UNVERIFIED.
    If a future refactor removes it, the orchestrator handler can be
    removed too — but the gate-side change must come first."""
    assert 'return "VISUAL_UNVERIFIED"' in _GATE_SRC, (
        "reviewer_gate.py must still emit VISUAL_UNVERIFIED — otherwise "
        "the orchestrator handler is dead code"
    )


# ---------------------------------------------------------------------------
# R9: unknown verdict → HALTED_SILENT (the safety property)
# ---------------------------------------------------------------------------


def test_unknown_verdict_transitions_to_halted_silent():
    """Source-level guard: the orchestrator must reference HALTED_SILENT
    in the reviewer-gate consumption block so an unrecognised verdict
    transitions to a loud halt instead of falling through.

    We look for the literal token pair near the reviewer-gate verdict
    chain.
    """
    # The verdict-handling block lives near `run_reviewer_output_gate()`
    # call.  Slice ~3000 chars around it.
    idx = _ORCH_SRC.find("run_reviewer_output_gate()")
    assert idx != -1, "Could not locate reviewer-gate consumption block"
    # Slice up to the next major branch (escalation agent block) — the
    # reviewer-gate dispatch chain is large (~700 lines) due to INFRA_FAILURE
    # recovery branching, so a fixed character window is too narrow.
    end = _ORCH_SRC.find('elif current_agent == "escalation"', idx)
    if end == -1:
        end = idx + 20000
    window = _ORCH_SRC[idx:end]
    halt_pat = re.compile(
        r"HALTED_SILENT[\s\S]{0,300}?(unknown|unrecognized|unrecognised)"
        r"|((unknown|unrecognized|unrecognised)[\s\S]{0,300}?HALTED_SILENT)",
        re.IGNORECASE,
    )
    assert halt_pat.search(window), (
        "reviewer-gate consumption block must transition to HALTED_SILENT "
        "for unknown/unrecognised verdicts so silent fall-through is "
        "impossible"
    )


# ---------------------------------------------------------------------------
# R3: ROUTE_EXECUTOR writes failure_context.json with blocking issues
# ---------------------------------------------------------------------------


def test_route_executor_writes_failure_context_atomically(
    monkeypatch, tmp_path
):
    """The ROUTE_EXECUTOR handler must write ``failure_context.json``
    containing the reviewer's blocking issues so the next executor pass
    can act on them.  Atomic-write semantics (mkstemp + os.replace) are
    used elsewhere — this write must match.

    We exercise the helper ``_write_reviewer_failure_context`` if present,
    or fall back to a source-level check that the ROUTE_EXECUTOR branch
    writes to ``failure_context.json`` with ``source=='reviewer'``.
    """
    # Helper-based test path (preferred): if the orchestrator exposes a
    # method specifically for writing the reviewer's failure context,
    # exercise it directly.
    helper = getattr(
        orch_mod.Orchestrator, "_write_reviewer_failure_context", None
    )
    if callable(helper):
        # Set up a bare orchestrator pointing at tmp_path for artifacts.
        monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
        orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        orch.state = {"current_phase_raw_id": "CORE-E6"}
        helper(
            orch,
            blocking_issues=["Test imports are wrong", "Coverage too low"],
            reviewer_summary="Two blocking issues",
            reviewer_pass=1,
        )
        ctx_path = tmp_path / "failure_context.json"
        assert ctx_path.exists(), "failure_context.json must be written"
        ctx = json.loads(ctx_path.read_text())
        assert ctx.get("source") == "reviewer"
        assert ctx.get("blocking_issues") == [
            "Test imports are wrong",
            "Coverage too low",
        ]
        assert ctx.get("reviewer_pass") == 1
        assert ctx.get("phase_id") == "CORE-E6"
        return

    # Source-level fallback: at minimum the ROUTE_EXECUTOR branch must
    # mention failure_context.json and blocking_issues.
    idx = _ORCH_SRC.find("ROUTE_EXECUTOR")
    assert idx != -1
    # Find the elif branch body — scan forward up to the next elif.
    next_elif = _ORCH_SRC.find("elif gate_result ==", idx + 10)
    block_end = next_elif if next_elif != -1 else idx + 2000
    branch = _ORCH_SRC[idx : block_end]
    assert "failure_context" in branch, (
        "ROUTE_EXECUTOR branch must write the reviewer's blocking-issue "
        "context into failure_context.json so the executor knows what to "
        "fix on the next pass — required for the routing handoff to be "
        "useful (current code re-runs executor blind)"
    )
    assert "blocking_issues" in branch, (
        "ROUTE_EXECUTOR branch must propagate the reviewer's "
        "blocking_issues list to the executor's failure context"
    )


# ---------------------------------------------------------------------------
# R10: reset_execution state-sync between phase_state and self.state
# ---------------------------------------------------------------------------


class TestResetExecutionStateSync:
    """``reset_execution`` was zeroing ``phase_state["reviewer_retries"]``
    but not ``self.state["reviewer_retries"]``, drifting the two state
    files apart across retries.  Fixing this is straightforward —
    update both on the same code path — and the regression test pins
    that both are zero after a reset.
    """

    def _bare_orch(self, tmp_path, monkeypatch):
        """Return an Orchestrator-shaped object with state files in
        ``tmp_path``.  Sufficient for exercising reset_execution's
        bookkeeping branch (we monkeypatch git operations to no-ops)."""
        monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
        # Minimal phase_state to begin with.
        (tmp_path / "phase_state.json").write_text(
            json.dumps({"reviewer_retries": 2, "reviewer_rejected": True})
        )
        orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        orch.state = {
            "reviewer_retries": 2,
            "executor_retries": 1,
            "current_phase": 1,
            "current_phase_raw_id": "CORE-E1",
            "project_path": str(tmp_path),
            "status": "RUNNING",
            "pipeline_status": "RUNNING",
        }
        orch.openclaw_config = {}
        orch.lock_fd = None
        # No-op git / subprocess calls so reset_execution doesn't try to
        # talk to the project repo.
        import subprocess as _sp

        monkeypatch.setattr(_sp, "run", lambda *a, **k: types_simple(returncode=0))
        # State writers — use the real implementations (they write to tmp_path).
        return orch

    def test_reset_execution_zeros_reviewer_retries_in_both_state_files(
        self, tmp_path, monkeypatch
    ):
        """After ``reset_execution('auto')`` both files must show
        ``reviewer_retries == 0`` — they were diverging before."""
        # Bare orchestrator with state files in tmp_path.
        monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
        monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
        monkeypatch.setattr(
            orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")
        )
        monkeypatch.setattr(
            orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json")
        )
        (tmp_path / "phase_state.json").write_text(
            json.dumps(
                {
                    "reviewer_retries": 2,
                    "reviewer_rejected": True,
                    "executor_retries": 0,
                }
            )
        )

        orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        orch.state = {
            "reviewer_retries": 2,
            "executor_retries": 1,
            "current_phase": 1,
            "current_phase_raw_id": "CORE-E1",
            "project_path": str(tmp_path),
            "status": "RUNNING",
            "pipeline_status": "RUNNING",
            "current_agent": "executor",
        }
        orch.openclaw_config = {}
        orch.lock_fd = None

        # Stub git / subprocess away.
        import subprocess as _sp

        class _R:
            returncode = 0

        monkeypatch.setattr(_sp, "run", lambda *a, **k: _R())

        orch.reset_execution("auto")

        # phase_state side
        ps = json.loads((tmp_path / "phase_state.json").read_text())
        assert ps.get("reviewer_retries") == 0, (
            "phase_state.reviewer_retries must be 0 after reset_execution"
        )
        # in-memory state side — the bug was that this was left untouched
        assert orch.state.get("reviewer_retries") == 0, (
            "self.state['reviewer_retries'] must also be zeroed so the two "
            "state files do not drift across retries (regression: was being "
            "left at its prior value)"
        )


def types_simple(**kw):
    """Tiny stand-in for subprocess.CompletedProcess used by the helper above."""
    class _C:
        pass

    c = _C()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ---------------------------------------------------------------------------
# R11: [REVIEWER_GATE] diagnostic log emitted on consumption
# ---------------------------------------------------------------------------


def test_reviewer_gate_diagnostic_log_emitted():
    """Source-level check: the reviewer-gate consumption block emits a
    canonical ``[REVIEWER_GATE] verdict=...`` log line so operators can
    reconstruct routing from logs without reading state files."""
    idx = _ORCH_SRC.find("run_reviewer_output_gate()")
    assert idx != -1
    window = _ORCH_SRC[idx : idx + 6000]
    pat = re.compile(r"\[REVIEWER_GATE\]\s*verdict=")
    assert pat.search(window), (
        "reviewer-gate consumption block must emit '[REVIEWER_GATE] "
        "verdict=…' log line for every dispatch so operators can see "
        "the routing decision in /tmp/orchestrator.log"
    )
