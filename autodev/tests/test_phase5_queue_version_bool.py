"""Phase 5 — T5.5: queue-version type safety + CAS ``mutate_fn`` purity.

Two defects in ``queue_semantics.py``:
  * ``isinstance(True, int)`` is ``True`` in Python, so a hand-edited / legacy
    ``"queue_version": true`` was read as ``1`` and ``bump_queue_version`` then
    wrote ``True + 1`` — the type flips to ``int`` mid-stream, masking the bad
    value. ``read_queue_version`` must reject bools.
  * The CAS contract says ``mutate_fn`` MUST NOT touch the version key (it is
    owned by ``bump_queue_version``), but that was convention-only. A future
    side-effecting closure would be re-fired up to 8× under contention and the
    version would drift, invisible to single-writer tests. ``mutate_queue`` now
    raises if a closure mutates the version key.
"""

import copy

import pytest

from queue_semantics import (  # noqa: E402 - sys.path wired by conftest
    QUEUE_VERSION_KEY,
    QueueAbort,
    bump_queue_version,
    mutate_queue,
    read_queue_version,
)


class TestReadQueueVersionRejectsBool:
    def test_rejects_bool_true(self):
        result = read_queue_version({QUEUE_VERSION_KEY: True})
        assert result == 0 and not isinstance(result, bool)

    def test_rejects_bool_false(self):
        # ``False == 0`` in Python, so assert the *type* is normalised to int too —
        # otherwise a leaked bool would pass a bare ``== 0`` check.
        result = read_queue_version({QUEUE_VERSION_KEY: False})
        assert result == 0 and not isinstance(result, bool)

    def test_accepts_valid_int(self):
        assert read_queue_version({QUEUE_VERSION_KEY: 5}) == 5

    def test_missing_key_is_zero(self):
        assert read_queue_version({}) == 0

    def test_negative_is_zero(self):
        assert read_queue_version({QUEUE_VERSION_KEY: -3}) == 0

    def test_non_numeric_is_zero(self):
        assert read_queue_version({QUEUE_VERSION_KEY: "7"}) == 0


class TestBumpNormalizesBool:
    def test_bump_after_bool_yields_int_one(self):
        data = {QUEUE_VERSION_KEY: True}
        bump_queue_version(data)
        # read_queue_version(True) -> 0, so bump writes 0 + 1 = 1 (a real int),
        # NOT True + 1 == 2 with the type flipped.
        assert data[QUEUE_VERSION_KEY] == 1
        assert type(data[QUEUE_VERSION_KEY]) is int


class TestMutateQueuePurity:
    @staticmethod
    def _io(store):
        """Return (read_fn, write_fn, current_version_fn) backed by an in-memory store."""
        def read_fn():
            return copy.deepcopy(store)

        def current_version_fn():
            return read_queue_version(store)

        def write_fn(data):
            bump_queue_version(data)
            store.clear()
            store.update(data)

        return read_fn, write_fn, current_version_fn

    def test_raises_when_closure_mutates_version(self):
        store = {QUEUE_VERSION_KEY: 2, "queue": [{"id": "a", "state": "READY"}]}
        read_fn, write_fn, current_version_fn = self._io(store)

        def bad(data):
            data[QUEUE_VERSION_KEY] = 999  # forbidden: version is bump's alone
            return "should-not-commit"

        with pytest.raises(RuntimeError):
            mutate_queue(read_fn, write_fn, current_version_fn, bad)
        # The bad write must not have committed.
        assert store[QUEUE_VERSION_KEY] == 2

    def test_pure_closure_commits_and_bumps(self):
        store = {QUEUE_VERSION_KEY: 2, "queue": [{"id": "a", "state": "READY"}]}
        read_fn, write_fn, current_version_fn = self._io(store)

        def good(data):
            data["queue"][0]["state"] = "ACTIVE"
            return True

        result = mutate_queue(read_fn, write_fn, current_version_fn, good)
        assert result is True
        assert store["queue"][0]["state"] == "ACTIVE"
        assert store[QUEUE_VERSION_KEY] == 3  # bumped exactly once

    def test_abort_short_circuits_before_purity_check(self):
        store = {QUEUE_VERSION_KEY: 2, "queue": []}
        read_fn, write_fn, current_version_fn = self._io(store)

        def abort(data):
            raise QueueAbort()

        assert mutate_queue(read_fn, write_fn, current_version_fn, abort) is None
        assert store[QUEUE_VERSION_KEY] == 2  # untouched
