"""P1 Stage D — orchestrator handler for BEHAVIORAL_UNVERIFIED (pooled-counter form).

Stage D consolidates the three contract-shape handlers (VISUAL_UNVERIFIED,
BEHAVIORAL_UNVERIFIED, REGRESSION_UNVERIFIED) into a single parameterised
``elif gate_result in (...):`` branch driving a pooled
``reviewer_unverified_retries`` counter (cap 2 across all three).

Previously (P0 Stage F), this file pinned a per-flavour
``reviewer_behavioral_retries`` counter against a dedicated handler. Both
the counter and the dedicated handler are GONE after Stage D — those
assertions were tests of dead behaviour, so they have been replaced with
assertions on the unified shape.

Filename kept so operators searching for "behavioral" still find this surface.
"""

import os
import re

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")

_ORCH_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")
with open(_ORCH_PATH, "r", encoding="utf-8") as _f:
    _ORCH_SRC = _f.read()


def _unverified_handler_window():
    """Return the slice of orchestrator.py containing the parameterised
    UNVERIFIED handler (the consolidated branch covering all three
    contract-shape verdicts).

    Pre-Stage-D this returned the BEHAVIORAL_UNVERIFIED-only handler; the
    consolidation means the handler is now a single tuple-dispatched branch."""
    pat = re.compile(r"elif gate_result in \(")
    m = pat.search(_ORCH_SRC)
    assert m is not None, (
        "orchestrator.py must contain a parameterised UNVERIFIED handler "
        "(`elif gate_result in (...):`) covering BEHAVIORAL_UNVERIFIED and "
        "its sibling contract-shape verdicts. The per-flavour "
        "BEHAVIORAL_UNVERIFIED handler from P0 Stage F was deleted in P1 "
        "Stage D's consolidation."
    )
    start_idx = m.start()
    next_elif = _ORCH_SRC.find("elif gate_result ==", start_idx + 10)
    next_else = _ORCH_SRC.find("\n                    else:", start_idx + 10)
    candidates = [c for c in (next_elif, next_else) if c != -1]
    end_idx = min(candidates) if candidates else start_idx + 5000
    return _ORCH_SRC[start_idx:end_idx]


def test_behavioral_unverified_handled_by_unified_handler():
    """BEHAVIORAL_UNVERIFIED must be one of the verdicts dispatched by the
    parameterised handler. Pre-Stage-D this was a per-flavour handler; after
    Stage D it shares the consolidated branch with VISUAL_UNVERIFIED and
    REGRESSION_UNVERIFIED."""
    window = _unverified_handler_window()
    header = window.split(":", 1)[0]
    assert '"BEHAVIORAL_UNVERIFIED"' in header, (
        f"BEHAVIORAL_UNVERIFIED must appear in the parameterised handler's "
        f"verdict tuple. Header was: {header!r}"
    )


def test_behavioral_unverified_uses_pooled_counter_not_per_flavour():
    """The handler must use the pooled ``reviewer_unverified_retries`` counter,
    NOT the deleted ``reviewer_behavioral_retries`` per-flavour counter.

    Pre-Stage-D contract pinned a per-flavour counter. Stage D's
    consolidation pools all three contract-shape verdicts onto one counter
    — keeping the per-flavour counter would be sprawl."""
    window = _unverified_handler_window()
    assert "reviewer_unverified_retries" in window, (
        "Parameterised handler must use the pooled "
        "reviewer_unverified_retries counter — without this, the three "
        "contract-shape verdicts cannot share a budget and the consolidation "
        "is incomplete."
    )
    assert "reviewer_behavioral_retries" not in window, (
        "Per-flavour reviewer_behavioral_retries counter must NOT appear in "
        "the consolidated handler — that's the dead behaviour the "
        "consolidation deletes."
    )


def test_unified_handler_writes_unverified_instruction_to_phase_state():
    """The handler must persist a remediation instruction so the next reviewer
    invocation sees what was missing. Stage D renames ``behavioral_instruction``
    (per-flavour) to ``unverified_instruction`` (pooled) — the per-flavour
    field is dead behaviour after consolidation."""
    window = _unverified_handler_window()
    assert "unverified_instruction" in window, (
        "Parameterised handler must write an unverified_instruction field to "
        "phase_state so the re-invoked reviewer sees what was missing. "
        "Stage D replaces the per-flavour visual_instruction / "
        "behavioral_instruction fields with this single pooled field."
    )
    assert "behavioral_instruction" not in window, (
        "Per-flavour behavioral_instruction must NOT appear in the consolidated "
        "handler — it's the dead phase-state field Stage D removes."
    )


def test_unified_handler_caps_pooled_counter_at_two_then_escalates():
    """Cap unchanged at 2. The pooled counter inherits the threshold the
    per-flavour design used."""
    window = _unverified_handler_window()
    cap_pat = re.compile(
        r"reviewer_unverified_retries.*?>=\s*2", re.DOTALL
    )
    assert cap_pat.search(window) or re.search(r">=\s*2", window), (
        "Parameterised handler must cap pooled retries at 2 (mirror of the "
        "per-flavour cap from P0 Stage F)."
    )
    assert "escalation" in window, (
        "Parameterised handler must route to escalation when the pooled "
        "retry cap is reached."
    )


def test_behavioral_unverified_in_verdict_routing_table():
    """The verdict-to-next-agent diagnostic dispatch table (printed via
    ``[REVIEWER_GATE] verdict=… next_agent=…``) must still include
    BEHAVIORAL_UNVERIFIED → "reviewer". The dispatch-table entry survives
    the consolidation because each verdict still needs an explicit operator-
    facing next_agent mapping."""
    pat = re.compile(r'"BEHAVIORAL_UNVERIFIED"\s*:\s*"reviewer"')
    assert pat.search(_ORCH_SRC), (
        "orchestrator.py verdict→next-agent dispatch table must include "
        '"BEHAVIORAL_UNVERIFIED": "reviewer" so the [REVIEWER_GATE] '
        "diagnostic log line names the right next_agent. The dispatch "
        "table is operator-facing and per-verdict — pooling counters does "
        "not collapse the entries."
    )
