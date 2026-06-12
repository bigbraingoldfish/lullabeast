"""EX-RR: Executor valid-output guard at retries-exhausted boundary.

When executor_retries >= 3 (the retry cap), the orchestrator must check whether a
valid executor output is already on disk BEFORE escalating. This handles the
'orphaned session' scenario: a background executor session completes correctly
after the orchestrator's sentinel poll times out, leaving valid output on disk.
Without this guard the valid output is silently ignored and the phase escalates
unnecessarily, consuming the reviewer opportunity.

Scenario that triggered this fix (pulse / INFRA-E1, Apr 27 2026):
  - Attempt 3 webhook sent at 16:54:19
  - Orchestrator poll timed out / no-activity-detected at ~16:55
  - Escalation invoked 16:55
  - Executor (background) completed and wrote valid output at 16:57
  - Gate passes on the 16:57 output, but orchestrator had already moved on

(Blame attribution was removed — exhaustion now escalates directly — but the
salvage-before-escalation ordering this file pins is unchanged.)

FIND-ID: EX-RR
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "autodev" / "pipeline"
ORCHESTRATOR_PATH = PIPELINE_DIR / "orchestrator.py"

for _p in (str(PIPELINE_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


EXHAUSTION_MARKER = "Executor retries exhausted. Escalating."


def _source() -> str:
    return ORCHESTRATOR_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_executor_retries_exhausted_has_gate_check_before_escalation():
    """EX-RR: run_executor_output_gate must be called BEFORE escalating.

    In the 'if retries >= 3' executor block, a gate validity check must appear
    before the 'write_failure_context / escalate' path so that an orphaned
    background session that completed successfully can still advance to
    reviewer rather than triggering unnecessary escalation.
    """
    src = _source()

    gate_marker = "run_executor_output_gate"

    exhaustion_pos = src.find(EXHAUSTION_MARKER)
    assert exhaustion_pos != -1, (
        f"Expected string '{EXHAUSTION_MARKER}' not found in orchestrator.py. "
        "Has the executor retries-exhausted block been restructured?"
    )

    # The gate check should appear within the retries>=3 block, which starts
    # just before the exhaustion marker.  Search up to 2000 chars before it.
    search_window = src[max(0, exhaustion_pos - 2000) : exhaustion_pos]
    gate_in_window = gate_marker in search_window

    assert gate_in_window, (
        f"run_executor_output_gate() does not appear within 2000 characters before "
        f"'{EXHAUSTION_MARKER}' in orchestrator.py.\n"
        "Fix: inside the 'if retries >= 3' executor block, add a surviving-output "
        "check BEFORE escalating:\n"
        "  _ex_rr_sentinel = os.path.join(PROJECT_ARTIFACTS_DIR, 'executor_output.done')\n"
        "  _ex_rr_json    = os.path.join(PROJECT_ARTIFACTS_DIR, 'executor_output.json')\n"
        "  if os.path.exists(_ex_rr_sentinel) and os.path.exists(_ex_rr_json):\n"
        "      if self.run_executor_output_gate():\n"
        "          # advance to reviewer, continue\n"
        "(FIND-ID: EX-RR)"
    )


def test_executor_retries_exhausted_advances_to_reviewer_on_valid_output():
    """EX-RR: The gate-passes branch must set current_agent to 'reviewer'.

    When the surviving-output gate check passes, the orchestrator must route to
    reviewer (not escalation), so the successful executor output is not wasted.
    """
    src = _source()

    exhaustion_pos = src.find(EXHAUSTION_MARKER)
    assert exhaustion_pos != -1

    # Inspect the 2000 chars before the escalation for a
    # 'current_agent' = 'reviewer' assignment inside the EX-RR guard.
    window = src[max(0, exhaustion_pos - 2000) : exhaustion_pos]

    # Either a string literal "reviewer" assignment or transition call
    reviewer_routed = '"reviewer"' in window or "'reviewer'" in window

    assert reviewer_routed, (
        "The surviving-output guard in the retries>=3 executor block must route to "
        "'reviewer' when the gate passes.  Ensure self.state['current_agent'] = 'reviewer' "
        "(or an equivalent transition call) is present before the escalation path."
        "(FIND-ID: EX-RR)"
    )


def test_executor_retries_exhausted_resets_retry_counter_on_valid_output():
    """EX-RR: executor_retries must be reset to 0 when the surviving-output gate passes.

    If retries are left at 3 while current_agent is set to 'reviewer', a subsequent
    restart would see retries>=3 again and loop into the exhausted block immediately.
    """
    src = _source()

    exhaustion_pos = src.find(EXHAUSTION_MARKER)
    assert exhaustion_pos != -1

    window = src[max(0, exhaustion_pos - 2000) : exhaustion_pos]

    # The counter reset can appear as:
    #   self.state["executor_retries"] = 0
    #   OR  state["executor_retries"] = 0
    has_reset = 'executor_retries"] = 0' in window or "executor_retries'] = 0" in window

    assert has_reset, (
        "When the EX-RR surviving-output gate passes, executor_retries must be reset "
        "to 0 (e.g. self.state['executor_retries'] = 0) before continuing. "
        "Without this reset a fresh orchestrator restart would immediately re-enter "
        "the retries-exhausted block and loop forever."
        "(FIND-ID: EX-RR)"
    )
