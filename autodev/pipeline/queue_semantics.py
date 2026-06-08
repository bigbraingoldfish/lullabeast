"""Shared queue dependency rules + write-concurrency primitives (orchestrator + UI server).

DEPENDENCY_HOLD is only for children whose parent is in a blocking queue state.
Children still wait for parent COMPLETED before they can start (next_eligible /
_select_next_queue_project skip), but stay READY while parent is ACTIVE/READY.

F9 — optimistic-concurrency for ``pipeline_queue.json``. Two independent OS processes write
that file (the UI server and the spawned orchestrator) with no shared lock. ``os.replace``
gives atomic *file* replacement but last-full-write-wins semantics, so a naive read→mutate→
write on each side silently drops the other's update. ``mutate_queue`` below is the single
read→apply→compare-and-swap→retry loop both processes drive (orchestrator ``_mutate_queue`` /
server ``_mutate_queue_file``); the ``queue_version`` integer is the CAS token.
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


# ---------------------------------------------------------------------------
# Parked-entry metadata hygiene (Defect C — shared by orchestrator + server)
# ---------------------------------------------------------------------------

# The full set of per-entry keys that _queue_park_active_entry writes when it parks
# the ACTIVE row (ESCALATION / BLOCKED). Any transition that takes a row OUT of a
# parked state without going through proper revival must remove ALL of these, or the
# entry drifts (e.g. state=READY still carrying a stale parked_state_snapshot — the
# Minecraft inconsistency). Defined once here so the orchestrator's selection/restore
# paths and the server's demote/promote reconcile share one canonical set and cannot
# silently diverge (the prior bug: _queue_restore_parked_entry_to_active scrubbed only
# 3 of these, and the server's reconcile scrubbed none).
PARKED_ENTRY_FIELDS = frozenset({
    "parked_state_snapshot",
    "parked_at",
    "parked_reason",
    "parked_pipeline_status",
    "answered_at",
})


def scrub_parked_fields(entry: dict) -> bool:
    """Remove every PARKED_ENTRY_FIELDS key from *entry* in place.

    Returns True if any field was present (so callers can fold it into their
    ``changed`` tracking). Pure and id-free — safe to call inside an F9 CAS
    closure, which may re-apply it onto a freshly-read dict on retry.
    """
    changed = False
    for key in PARKED_ENTRY_FIELDS:
        if key in entry:
            del entry[key]
            changed = True
    return changed


# ---------------------------------------------------------------------------
# F9 — queue write concurrency (optimistic version-CAS)
# ---------------------------------------------------------------------------

# Monotonic integer stamped on every queue write; the compare-and-swap token. A legacy file
# (written before F9) has no such key — read_queue_version treats that as 0, and the first
# CAS write lands version 1, so no migration step is needed (additive schema).
QUEUE_VERSION_KEY = "queue_version"

# Upper bound on CAS re-tries before giving up. With only two low-frequency writers on a single
# host, >2-3 genuine conflicts is astronomically unlikely; 8 is generous headroom and still
# terminates promptly (the orchestrator logs the QueueVersionConflict; the server maps it to 503).
QUEUE_MAX_CAS_RETRIES = 8


class QueueVersionConflict(RuntimeError):
    """Raised by mutate_queue when the CAS retry budget is exhausted (perpetual contention)."""


class QueueAbort(Exception):
    """Raised by a mutate_fn to abort the write entirely (mutate_queue returns None, no write).

    Used by the complex call sites (selection / trigger-next) when, on a re-read, the entry they
    intended to mutate has vanished or is no longer in the expected pre-state — i.e. a concurrent
    writer changed the very row this mutation targeted. Aborting (rather than forcing a write) is
    the existing "leave it for the next cycle" idiom, now made explicit.
    """


def read_queue_version(data: dict) -> int:
    """Return the queue's version, treating a missing/legacy/invalid value as 0.

    ``bool`` is explicitly excluded even though ``isinstance(True, int)`` is
    ``True``: a hand-edited / legacy ``"queue_version": true`` would otherwise
    read as ``1`` and ``bump_queue_version`` would write ``True + 1`` (the type
    silently flips mid-stream). A bool — like any non-int / negative value — is
    normalised to ``0`` so the first real CAS write lands a clean ``1``.
    """
    v = data.get(QUEUE_VERSION_KEY)
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def bump_queue_version(data: dict) -> None:
    """Stamp ``data`` with the next version (in place). The single +1 site — every writer
    (orchestrator ``_write_queue`` / server ``_write_queue_file``) calls this so the increment
    rule lives in exactly one place and ``mutate_fn`` never touches the version itself."""
    data[QUEUE_VERSION_KEY] = read_queue_version(data) + 1


def mutate_queue(read_fn, write_fn, current_version_fn, mutate_fn,
                 *, max_retries: int = QUEUE_MAX_CAS_RETRIES):
    """Read → capture base version → apply *mutate_fn* → compare-and-swap → retry on conflict.

    All I/O is injected, so this loop is pure and unit-testable in isolation:
      * ``read_fn()``            -> a fresh full queue dict read from disk.
      * ``current_version_fn()`` -> the version currently on disk (the cheap "compare" read).
      * ``mutate_fn(data)``      -> applies the caller's in-memory change to *data* and returns
                                    the call's result. It MUST be idempotent when re-applied onto
                                    a freshly-read dict (re-derive targets by id, do not rely on a
                                    stale snapshot), and MUST raise ``QueueAbort`` to bail out with
                                    no write. It MUST NOT touch the version key.
      * ``write_fn(data)``       -> the existing atomic writer (it calls ``bump_queue_version`` →
                                    persists base+1).

    Because ``os.replace`` is unconditional and there is deliberately NO file lock (F9 locked
    "no new lock" to avoid nesting against ``pipeline.lock``), the compare is done in userspace:
    we re-read the on-disk version immediately before writing and only commit if it still equals
    the base we read. This shrinks the lost-update window from "the whole request" to the
    microseconds between that re-read and ``os.replace``. That residual micro-window is real —
    lock-free CAS on a bare file cannot mathematically close it without a lock or a per-write
    nonce (a writer-unique token + post-replace read-back, the documented deferred follow-up).
    For two low-frequency writers on one host it is negligible, and any genuine conflict is caught
    and retried here rather than silently dropped.

    Raises QueueVersionConflict if *max_retries* conflicts occur without a clean commit.

    The version key is owned exclusively by ``bump_queue_version`` (called inside
    ``write_fn``). A ``mutate_fn`` that touches it would be re-applied — and the
    version re-bumped from a corrupted base — on every CAS retry, so this loop
    enforces that invariant: if ``mutate_fn`` changed the version key, it raises
    ``RuntimeError`` rather than committing. (Full closure purity — no spawn,
    symlink, or other I/O in the retried region — cannot be enforced in Python;
    this checks the one violation that is provable and most damaging.)
    """
    for _ in range(max_retries):
        data = read_fn()
        base = read_queue_version(data)
        try:
            result = mutate_fn(data)
        except QueueAbort:
            return None
        if read_queue_version(data) != base:
            raise RuntimeError(
                "mutate_fn must not modify the queue_version key — it is owned by "
                f"bump_queue_version (base={base}, after mutate_fn="
                f"{data.get(QUEUE_VERSION_KEY)!r})"
            )
        if current_version_fn() == base:   # nobody else wrote since our read -> safe to commit
            write_fn(data)                 # write_fn bumps base -> base+1
            return result
        # on-disk version moved under us -> re-read and re-apply onto the fresh base
    raise QueueVersionConflict(f"queue CAS exceeded {max_retries} retries")
