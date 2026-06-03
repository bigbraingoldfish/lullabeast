"""P1 Stage D — orchestrator parameterised UNVERIFIED handler.

The pre-Stage-D orchestrator had two separate handlers — VISUAL_UNVERIFIED
(its own counter ``reviewer_visual_retries``) and BEHAVIORAL_UNVERIFIED
(its own counter ``reviewer_behavioral_retries``). Stage D adds a third
contract-shape verdict (REGRESSION_UNVERIFIED) and consolidates all three
handlers into one parameterised branch driving a single pooled counter
``reviewer_unverified_retries`` (cap 2 across all three).

These are source-level + handler-shape tests on the orchestrator file,
mirror of ``test_orchestrator_behavioral_unverified.py``'s structural
patterns. The handler must:

* Dispatch all three verdicts from a single branch.
* Increment the pooled counter, NOT any per-flavour counter.
* Escalate at the pooled cap of 2.
* Add REGRESSION_UNVERIFIED to the verdict→agent diagnostic dispatch table.
* Leave no per-flavour counter strings anywhere in orchestrator.py (removal
  completeness — catches a future change that reintroduces them).
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
    UNVERIFIED handler. The handler starts at
    ``elif gate_result in (`` (the only branch that dispatches multiple
    verdict strings at once) and ends at the next ``elif gate_result ==`` or
    ``else:`` at the same indent."""
    # Look for the parameterised tuple form. After consolidation the handler
    # uses `elif gate_result in (...)`; before consolidation, the regex
    # search returns nothing and the tests catch that as a failure.
    pat = re.compile(r"elif gate_result in \(")
    m = pat.search(_ORCH_SRC)
    assert m is not None, (
        "orchestrator.py must contain a parameterised UNVERIFIED handler "
        "(`elif gate_result in (...):`) covering VISUAL_UNVERIFIED, "
        "BEHAVIORAL_UNVERIFIED, REGRESSION_UNVERIFIED. The pre-Stage-D "
        "per-flavour handlers must be removed in the same pass."
    )
    start_idx = m.start()
    next_elif = _ORCH_SRC.find("elif gate_result ==", start_idx + 10)
    next_else = _ORCH_SRC.find("\n                    else:", start_idx + 10)
    candidates = [c for c in (next_elif, next_else) if c != -1]
    end_idx = min(candidates) if candidates else start_idx + 5000
    return _ORCH_SRC[start_idx:end_idx]


# ---------------------------------------------------------------------------
# §3 File 4 — five rows
# ---------------------------------------------------------------------------


def test_unified_handler_dispatches_regression_unverified():
    """The parameterised handler's verdict tuple must include
    REGRESSION_UNVERIFIED so the new verdict is dispatched alongside
    VISUAL_UNVERIFIED and BEHAVIORAL_UNVERIFIED.

    Without this, REGRESSION_UNVERIFIED falls through to the unknown-verdict
    HALTED_SILENT branch — every regression-shape failure halts the pipeline
    instead of re-invoking the reviewer."""
    window = _unverified_handler_window()
    # The tuple is in the elif header itself; grab the header line and verify
    # all three verdicts appear there.
    header = window.split(":", 1)[0]
    assert '"REGRESSION_UNVERIFIED"' in header, (
        f"REGRESSION_UNVERIFIED must appear in the parameterised handler's "
        f"verdict tuple — header was: {header!r}"
    )
    assert '"VISUAL_UNVERIFIED"' in header, (
        f"VISUAL_UNVERIFIED must remain in the parameterised handler — "
        f"the per-flavour handler was deleted; header was: {header!r}"
    )
    assert '"BEHAVIORAL_UNVERIFIED"' in header, (
        f"BEHAVIORAL_UNVERIFIED must remain in the parameterised handler — "
        f"the per-flavour handler was deleted; header was: {header!r}"
    )


def test_unified_handler_increments_pooled_counter_not_per_flavour():
    """The handler must read/write the pooled ``reviewer_unverified_retries``
    counter. Per-flavour counters (``reviewer_visual_retries``,
    ``reviewer_behavioral_retries``) must not appear inside the handler."""
    window = _unverified_handler_window()
    assert "reviewer_unverified_retries" in window, (
        "Parameterised handler must use the pooled counter "
        "reviewer_unverified_retries — without this, the three contract-shape "
        "verdicts cannot share a budget and the consolidation goal is lost."
    )
    assert "reviewer_visual_retries" not in window, (
        "Per-flavour visual counter must NOT appear inside the consolidated "
        "handler — sprawl is the bug Stage D's consolidation eliminates."
    )
    assert "reviewer_behavioral_retries" not in window, (
        "Per-flavour behavioural counter must NOT appear inside the "
        "consolidated handler — sprawl is the bug Stage D's consolidation "
        "eliminates."
    )


def test_unified_handler_escalates_at_pooled_cap_of_2():
    """Cap unchanged from the per-flavour design. Source-level checks:
    the handler contains a guard comparing the pooled counter to 2, and the
    escalation arm sets ``current_agent = "escalation"``."""
    window = _unverified_handler_window()
    cap_pat = re.compile(
        r"reviewer_unverified_retries.*?>=\s*2", re.DOTALL
    )
    assert cap_pat.search(window) or re.search(r">=\s*2", window), (
        "Parameterised handler must cap pooled retries at 2 — without a cap "
        "the re-invocation loop is unbounded. Each per-flavour handler "
        "previously capped at 2; the pool inherits the same threshold."
    )
    assert "escalation" in window, (
        "Parameterised handler must route to escalation when the pooled "
        "retry cap is reached."
    )


def test_verdict_to_agent_table_includes_regression_unverified():
    """The verdict-to-next-agent diagnostic dispatch table near
    ``run_reviewer_output_gate()`` must include
    ``"REGRESSION_UNVERIFIED": "reviewer"`` so the operator-visible
    ``[REVIEWER_GATE]`` log line names the correct next agent when the
    verdict fires."""
    pat = re.compile(r'"REGRESSION_UNVERIFIED"\s*:\s*"reviewer"')
    assert pat.search(_ORCH_SRC), (
        "orchestrator.py verdict→next-agent dispatch table must include "
        '"REGRESSION_UNVERIFIED": "reviewer" so the [REVIEWER_GATE] '
        "diagnostic log line names the right next_agent. Without this, "
        "operators see 'next_agent=halted' even though the handler "
        "re-invokes the reviewer."
    )


def test_legacy_counter_names_absent_from_orchestrator_source():
    """Removal completeness: ``reviewer_visual_retries`` and
    ``reviewer_behavioral_retries`` must not appear anywhere in
    orchestrator.py after consolidation. Catches a future change that
    reintroduces per-flavour counters.

    Likewise the per-flavour ``visual_instruction`` / ``behavioral_instruction``
    field names AND the intermediate pooled ``unverified_instruction`` field are
    all replaced by the unified, actually-delivered ``reviewer_retry_directive``
    (Phase 4 directive channel). ``unverified_instruction`` was written to
    phase_state but never read/delivered — a dead write that left the UNVERIFIED
    retries blind; it must be gone."""
    assert "reviewer_visual_retries" not in _ORCH_SRC, (
        "reviewer_visual_retries must be deleted from orchestrator.py — the "
        "pooled reviewer_unverified_retries replaces it. Comments and code "
        "alike: per-flavour-counter sprawl is the bug Stage D eliminates."
    )
    assert "reviewer_behavioral_retries" not in _ORCH_SRC, (
        "reviewer_behavioral_retries must be deleted from orchestrator.py — "
        "the pooled reviewer_unverified_retries replaces it."
    )
    assert "visual_instruction" not in _ORCH_SRC, (
        "visual_instruction phase-state field replaced by "
        "reviewer_retry_directive — leaving the old field name is dead-code "
        "residue that confuses operators reading phase_state.json."
    )
    assert "behavioral_instruction" not in _ORCH_SRC, (
        "behavioral_instruction phase-state field replaced by "
        "reviewer_retry_directive — same removal-completeness rule."
    )
    assert "unverified_instruction" not in _ORCH_SRC, (
        "unverified_instruction phase-state field replaced by the unified, "
        "delivered reviewer_retry_directive — it was a dead write (never read by "
        "any reader, never delivered to the reviewer), so it must be removed, not "
        "left as residue."
    )
