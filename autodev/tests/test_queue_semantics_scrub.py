"""Defect C (C1) — the shared parked_* scrub helper.

TDD: written before `scrub_parked_fields` / `PARKED_ENTRY_FIELDS` exist in
``queue_semantics.py``. The helper is the single source of truth for "which fields
park-metadata occupies", reused by both the orchestrator (selection + restore paths)
and the UI server (demote/promote reconcile) so the set can never drift between them.
"""
from queue_semantics import PARKED_ENTRY_FIELDS, scrub_parked_fields


def test_parked_entry_fields_is_the_canonical_five():
    """Pin the canonical set so a future edit that drops one (the 3-of-5 bug class)
    is caught here, not in production."""
    assert PARKED_ENTRY_FIELDS == frozenset({
        "parked_state_snapshot",
        "parked_at",
        "parked_reason",
        "parked_pipeline_status",
        "answered_at",
    })


def test_scrub_pops_all_five_and_returns_true():
    entry = {
        "id": "e1",
        "state": "ACTIVE",
        "parked_state_snapshot": {"current_phase_raw_id": "CORE-E1"},
        "parked_at": "2026-06-08T05:08:42+00:00",
        "parked_reason": "escalation",
        "parked_pipeline_status": "WAITING_FOR_HUMAN",
        "answered_at": "2026-06-08T06:00:00+00:00",
    }
    changed = scrub_parked_fields(entry)
    assert changed is True
    assert entry == {"id": "e1", "state": "ACTIVE"}  # only park metadata removed


def test_scrub_partial_set_pops_present_and_returns_true():
    """A row carrying only some park fields (e.g. _queue_restore's old 3-field
    partial) is still fully cleaned."""
    entry = {
        "id": "e2",
        "state": "READY",
        "parked_state_snapshot": {"x": 1},
        "answered_at": "2026-06-08T06:00:00+00:00",
    }
    changed = scrub_parked_fields(entry)
    assert changed is True
    assert "parked_state_snapshot" not in entry
    assert "answered_at" not in entry
    assert entry["state"] == "READY"


def test_scrub_clean_entry_returns_false_and_is_unchanged():
    entry = {"id": "e3", "state": "READY", "position": 2, "project_path": "/tmp/p"}
    before = dict(entry)
    changed = scrub_parked_fields(entry)
    assert changed is False
    assert entry == before
