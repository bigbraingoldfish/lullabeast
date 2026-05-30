"""Shared queue dependency rules (orchestrator + UI server).

DEPENDENCY_HOLD is only for children whose parent is in a blocking queue state.
Children still wait for parent COMPLETED before they can start (next_eligible /
_select_next_queue_project skip), but stay READY while parent is ACTIVE/READY.
"""

from __future__ import annotations

# P1 Stage H — a parked ESCALATION row whose operator answer has been banked
# (``pending_escalation_command.json`` present). The orchestrator promotes
# ESCALATION -> ESCALATION_ANSWERED during selection, then revives it: restores
# the parked phase pointer and applies the banked command. This is a queue-ENTRY
# state (``pipeline_queue.json`` entries' ``state``), NOT a ``pipeline_status``
# value — it deliberately does not appear in the orchestrator's VALID_STATES.
ESCALATION_ANSWERED = "ESCALATION_ANSWERED"

# Entry states that selection treats as revivable (restore pointer + apply banked
# command) rather than as a fresh phase-0 start.
REVIVABLE_ANSWERED_STATES = frozenset({ESCALATION_ANSWERED})

# Parent queue states that force children into DEPENDENCY_HOLD (cannot proceed until parent clears).
# ESCALATION_ANSWERED is included: an answered-but-not-yet-resumed parent has not COMPLETED,
# so a child must still hold until the parent's revival lands and the project finishes.
PARENT_BLOCKS_CHILD_STATES = frozenset({"BLOCKED", "ESCALATION", ESCALATION_ANSWERED})


def parent_blocks_child(parent_state: str | None) -> bool:
    """True if a child row should be DEPENDENCY_HOLD while linked to this parent."""
    if not parent_state:
        return False
    return parent_state in PARENT_BLOCKS_CHILD_STATES
