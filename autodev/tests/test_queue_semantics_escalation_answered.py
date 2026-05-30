"""P1 Stage H — queue_semantics groundwork for parked-escalation revival.

ESCALATION_ANSWERED is a new queue-ENTRY state: a parked ESCALATION row whose
operator answer has been banked and which is now eligible for revival. Until the
project actually resumes and completes, an answered parent still blocks its
children exactly like an un-answered ESCALATION or BLOCKED parent — so the new
state must be in PARENT_BLOCKS_CHILD_STATES.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import queue_semantics as qs


def test_escalation_answered_constant_value():
    """The constant is the canonical anchor for the new state string."""
    assert qs.ESCALATION_ANSWERED == "ESCALATION_ANSWERED"


def test_escalation_answered_is_revivable():
    assert "ESCALATION_ANSWERED" in qs.REVIVABLE_ANSWERED_STATES


def test_answered_parent_still_blocks_children():
    """An answered-but-not-yet-resumed parent has NOT completed; children must hold."""
    assert qs.parent_blocks_child("ESCALATION_ANSWERED") is True
    assert qs.ESCALATION_ANSWERED in qs.PARENT_BLOCKS_CHILD_STATES


def test_unanswered_and_blocked_parents_still_block():
    """Pre-existing semantics unchanged."""
    assert qs.parent_blocks_child("ESCALATION") is True
    assert qs.parent_blocks_child("BLOCKED") is True


def test_active_and_ready_parents_do_not_block():
    """Sanity: revivable/blocking set did not over-broaden."""
    assert qs.parent_blocks_child("ACTIVE") is False
    assert qs.parent_blocks_child("READY") is False
    assert qs.parent_blocks_child(None) is False
