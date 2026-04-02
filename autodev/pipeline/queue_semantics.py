"""Shared queue dependency rules (orchestrator + UI server).

DEPENDENCY_HOLD is only for children whose parent is in a blocking queue state.
Children still wait for parent COMPLETED before they can start (next_eligible /
_select_next_queue_project skip), but stay READY while parent is ACTIVE/READY.
"""

from __future__ import annotations

# Parent queue states that force children into DEPENDENCY_HOLD (cannot proceed until parent clears).
PARENT_BLOCKS_CHILD_STATES = frozenset({"BLOCKED", "ESCALATION"})


def parent_blocks_child(parent_state: str | None) -> bool:
    """True if a child row should be DEPENDENCY_HOLD while linked to this parent."""
    if not parent_state:
        return False
    return parent_state in PARENT_BLOCKS_CHILD_STATES
