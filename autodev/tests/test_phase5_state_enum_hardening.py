"""Phase 5 — state-enum & transition hardening (F5 + F12).

F5: ``IDLE`` is a reset/entry ``pipeline_status`` owned exclusively by external
writers (the UI / tooling) via a direct atomic write to ``pipeline_state.json``.
It is intentionally absent from ``VALID_STATES`` and is never a
``transition_state`` target. These tests lock that contract in code so a future
contributor cannot silently re-introduce the code/doc contradiction F5 closes.

F12: ``transition_state`` must FAIL LOUDLY (raise ``ValueError``) when handed a
status not in ``VALID_STATES``, instead of the old silent ``print + return``
no-op that left a caller's prior ``self.state`` mutation unpersisted and
un-rolled-back.
"""

import os
import re
from unittest.mock import MagicMock

import pytest

import orchestrator as orch_mod

# __file__ = <repo>/autodev/tests/test_phase5_state_enum_hardening.py
# three dirname() hops reach the repo root (matches the conftest convention).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORCHESTRATOR_PATH = os.path.join(REPO_ROOT, "autodev", "pipeline", "orchestrator.py")


def _orchestrator_source():
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _make_orchestrator():
    """Build a minimal Orchestrator for unit-testing ``transition_state`` in
    isolation.

    Bypasses ``__init__`` via ``__new__`` on purpose: the constructor validates
    OPENCLAW_ROOT, loads ``openclaw.json``, and builds a SkillManager — none of
    which ``transition_state`` touches. Skipping it keeps the test hermetic and
    order-independent (a full-suite run can otherwise leave the module-level
    ``OPENCLAW_ROOT`` pointing at another test's torn-down tmp dir). The method
    under test reads only ``self.state`` and calls ``self.write_state``.
    """
    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {"pipeline_status": "RUNNING", "last_action": "init"}
    return inst


# ---------------------------------------------------------------------------
# F5 — IDLE stays out of VALID_STATES and is documented external-only
# ---------------------------------------------------------------------------


def test_idle_not_in_valid_states():
    """Locked decision: IDLE must NOT be in VALID_STATES. It is an external-only
    reset status; adding it would make it a legal ``transition_state`` target and
    reopen the code/doc contradiction F5 closes. Catches a future 'helpful'
    addition."""
    assert "IDLE" not in orch_mod.VALID_STATES


def test_valid_states_comment_documents_idle_external_only():
    """The VALID_STATES block must be preceded by a comment that explains IDLE's
    external-only role, so the exclusion reads as deliberate (not an oversight).
    Lightweight source lint, same spirit as the render-map completeness lints.
    Fails today (no such comment exists yet)."""
    src = _orchestrator_source()
    idx = src.index("VALID_STATES = [")
    window = src[max(0, idx - 800):idx]
    comment_text = "\n".join(
        ln for ln in window.splitlines() if ln.lstrip().startswith("#")
    ).lower()
    assert "idle" in comment_text and "external" in comment_text, (
        "VALID_STATES must be preceded by a comment marking IDLE as an "
        "external-only reset status that is never a transition_state target"
    )


def test_no_transition_state_idle_call_site():
    """No code may route IDLE through ``transition_state`` — IDLE is set only by a
    direct external atomic write. Walk every repo .py file and assert the absence
    of ``transition_state("IDLE")`` / ``transition_state('IDLE')``."""
    pattern = re.compile(r"""transition_state\(\s*["']IDLE["']""")
    offenders = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [
            d
            for d in dirs
            if d not in {".git", ".venv", "venv", "__pycache__", "node_modules"}
        ]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            if os.path.abspath(path) == os.path.abspath(__file__):
                continue  # this test file references the pattern itself
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            if pattern.search(text):
                offenders.append(os.path.relpath(path, REPO_ROOT))
    assert not offenders, f"transition_state('IDLE') call site(s) found: {offenders}"


# ---------------------------------------------------------------------------
# F12 — transition_state raises (loud) on an invalid status
# ---------------------------------------------------------------------------


def test_transition_state_invalid_raises_and_does_not_write():
    """An unknown status must RAISE ValueError (loud), not silently no-op. Proves:
    (1) it raises, (2) no state is persisted (write_state not called), and
    (3) pipeline_status is left unchanged in memory (the validity check precedes
    any mutation). Fails today: the old code prints and returns silently."""
    inst = _make_orchestrator()
    write_spy = MagicMock()
    inst.write_state = write_spy

    with pytest.raises(ValueError):
        inst.transition_state("BOGUS", "should raise")

    write_spy.assert_not_called()
    assert inst.state["pipeline_status"] == "RUNNING"


def test_transition_state_valid_still_writes():
    """Regression guard: a valid status still transitions + persists exactly once.
    Proves the raise is gated strictly on the invalid branch and the happy path
    is untouched."""
    inst = _make_orchestrator()
    write_spy = MagicMock()
    inst.write_state = write_spy

    inst.transition_state("STOPPED", "clean halt")

    write_spy.assert_called_once()
    assert inst.state["pipeline_status"] == "STOPPED"
    assert inst.state["last_action"] == "clean halt"
