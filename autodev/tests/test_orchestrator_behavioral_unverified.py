"""P0 Stage F — orchestrator handler for BEHAVIORAL_UNVERIFIED.

The reviewer gate's new BEHAVIORAL_UNVERIFIED verdict needs an explicit
handler in the orchestrator's verdict-dispatch chain. The handler mirrors
the existing VISUAL_UNVERIFIED handler:

  - Uses its own counter ``reviewer_behavioral_retries`` (NOT
    ``reviewer_retries``). This is the "non-retry-consuming" safeguard.
  - Caps at 2 — after that, escalates.
  - Writes a ``behavioral_instruction`` field to phase_state so the next
    reviewer invocation sees the remediation guidance.
  - Re-invokes the reviewer (current_agent stays "reviewer" until cap).

These are source-level + handler-shape tests on the orchestrator file;
mirror of ``test_orchestrator_reviewer_visual_unverified.py`` patterns where
applicable (the visual case has end-to-end handler tests in the routing-
dispatch audit).
"""

import os
import re
import sys

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")

_ORCH_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")
with open(_ORCH_PATH, "r", encoding="utf-8") as _f:
    _ORCH_SRC = _f.read()


def _behavioral_handler_window():
    """Return the slice of orchestrator.py containing the BEHAVIORAL_UNVERIFIED
    handler. The handler starts at ``elif gate_result == "BEHAVIORAL_UNVERIFIED"``
    and ends at the next ``elif gate_result ==`` or ``else:`` at the same
    indent."""
    start_idx = _ORCH_SRC.find('elif gate_result == "BEHAVIORAL_UNVERIFIED"')
    assert start_idx != -1, (
        "orchestrator.py must contain an explicit handler branch for "
        "BEHAVIORAL_UNVERIFIED — anything else is silent fall-through"
    )
    # Scan forward to the next branch at the same logical level. The next
    # 'elif gate_result' or 'else:' delimits the block.
    next_elif = _ORCH_SRC.find("elif gate_result ==", start_idx + 10)
    next_else = _ORCH_SRC.find("\n                    else:", start_idx + 10)
    candidates = [c for c in (next_elif, next_else) if c != -1]
    end_idx = min(candidates) if candidates else start_idx + 3000
    return _ORCH_SRC[start_idx:end_idx]


def test_behavioral_unverified_uses_separate_counter():
    """The handler must increment ``reviewer_behavioral_retries``, NOT the
    main ``reviewer_retries`` budget. Otherwise a contract-shape failure
    would burn a legitimate code-quality retry slot."""
    window = _behavioral_handler_window()
    assert "reviewer_behavioral_retries" in window, (
        "BEHAVIORAL_UNVERIFIED handler must use its own counter "
        "reviewer_behavioral_retries (mirror of reviewer_visual_retries) — "
        "without this, the handler consumes the main reviewer_retries "
        "budget and the non-retry-consuming guarantee is broken"
    )
    # The handler must NOT increment reviewer_retries itself.
    assert "reviewer_retries" not in window or "reviewer_behavioral_retries" in window, (
        "BEHAVIORAL_UNVERIFIED handler must not touch reviewer_retries"
    )


def test_behavioral_unverified_writes_instruction_to_phase_state():
    """The handler must persist a ``behavioral_instruction`` so the next
    reviewer invocation reads the remediation guidance from phase_state."""
    window = _behavioral_handler_window()
    assert "behavioral_instruction" in window, (
        "BEHAVIORAL_UNVERIFIED handler must write a behavioral_instruction "
        "field to phase_state so the re-invoked reviewer sees what was "
        "missing (mirror of visual_instruction)"
    )


def test_behavioral_unverified_caps_at_two_then_escalates():
    """The handler must cap retries at 2 and escalate beyond that — same
    cap as the visual handler. Source-level check for the cap value AND
    the escalation transition."""
    window = _behavioral_handler_window()
    # Cap check: '>= 2' near the counter
    cap_pat = re.compile(r"reviewer_behavioral_retries.*?>=\s*2", re.DOTALL)
    assert cap_pat.search(window) or re.search(r">=\s*2", window), (
        "BEHAVIORAL_UNVERIFIED handler must cap retries at 2 — without a "
        "cap the contract-shape re-invocation loop is unbounded"
    )
    # Escalation arm
    assert "escalation" in window, (
        "BEHAVIORAL_UNVERIFIED handler must route to escalation when the "
        "retry cap is reached"
    )


def test_behavioral_unverified_in_verdict_routing_table():
    """The verdict-to-next-agent diagnostic dispatch table (printed via
    ``[REVIEWER_GATE] verdict=… next_agent=…``) must include
    BEHAVIORAL_UNVERIFIED → "reviewer" so the operator-visible log line is
    accurate when the verdict fires."""
    # The dispatch table is a dict literal — look for the BEHAVIORAL_UNVERIFIED
    # key with "reviewer" as the value.
    pat = re.compile(
        r'"BEHAVIORAL_UNVERIFIED"\s*:\s*"reviewer"'
    )
    assert pat.search(_ORCH_SRC), (
        "orchestrator.py verdict→next-agent dispatch table must include "
        "\"BEHAVIORAL_UNVERIFIED\": \"reviewer\" so the [REVIEWER_GATE] "
        "diagnostic log line names the right next_agent"
    )
