"""Defensive Hardening Phase 6 — Group 2: lock-before-state-write + transactional symlink.

TDD (tests before implementation):
  * T6.1 (orchestrator side) — ``acquire_lock()`` becomes IDEMPOTENT so it can be lifted into
    ``main()`` ahead of the ``apply_cli_*`` state writes while ``run()`` still calls it. Without
    idempotency a second ``os.open``+``flock`` from the same process is denied (BlockingIOError →
    sys.exit), so the lift would break every run. ``main()`` must acquire the lock BEFORE the
    first ``apply_cli_*`` call so a losing instance exits before mutating shared state.
  * T6.5 — ``update_symlink`` is transactional: a second-link failure rolls the first link back
    to its prior target so the two project symlinks are never left permanently divergent.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def orch_inst(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.lock_fd = None
    return inst, orch_mod


# ---------------------------------------------------------------------------
# T6.1 — acquire_lock idempotency + main() ordering
# ---------------------------------------------------------------------------

class TestAcquireLockIdempotent:
    def test_idempotent_when_fd_already_set(self, orch_inst, monkeypatch):
        inst, _mod = orch_inst
        inst.lock_fd = 42  # sentinel: lock already held by this process
        opened = MagicMock()
        monkeypatch.setattr("os.open", opened)
        inst.acquire_lock()
        opened.assert_not_called()  # short-circuited, did not re-open/re-flock
        assert inst.lock_fd == 42

    def test_runs_when_fd_none(self, orch_inst, monkeypatch, tmp_path):
        inst, mod = orch_inst
        lock_file = tmp_path / "pipeline.lock"
        monkeypatch.setattr(mod, "LOCK_FILE", str(lock_file))
        monkeypatch.setattr(mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
        inst.lock_fd = None
        inst.acquire_lock()
        assert inst.lock_fd is not None
        meta = json.loads(lock_file.read_text())
        assert meta["pid"] == os.getpid()
        inst.release_lock()

    def test_acquire_release_reacquire(self, orch_inst, monkeypatch, tmp_path):
        inst, mod = orch_inst
        lock_file = tmp_path / "pipeline.lock"
        monkeypatch.setattr(mod, "LOCK_FILE", str(lock_file))
        monkeypatch.setattr(mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
        inst.lock_fd = None
        inst.acquire_lock()
        assert inst.lock_fd is not None
        inst.release_lock()
        assert inst.lock_fd is None  # release nulls the fd
        inst.acquire_lock()  # must NOT short-circuit — fd was reset to None
        assert inst.lock_fd is not None
        inst.release_lock()


def test_flock_second_ofd_same_process_is_denied(tmp_path):
    """Platform fact that MAKES idempotency necessary: two open() calls on the same inode
    give two independent open file descriptions, so the second flock is denied even within
    one process (Linux/WSL2). Lifting acquire_lock without the idempotency guard would hit
    exactly this and sys.exit every run."""
    import fcntl
    p = tmp_path / "x.lock"
    fd1 = os.open(str(p), os.O_RDWR | os.O_CREAT)
    fd2 = os.open(str(p), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd1)
        os.close(fd2)


def test_main_acquires_lock_before_apply_cli():
    """Source guard: in the __main__ block, orchestrator.acquire_lock() must textually precede
    the first apply_cli_* call so a losing instance exits before any state/symlink/queue write."""
    import re
    src = open(os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8").read()
    main_block = src.split('if __name__ == "__main__":', 1)[1]
    # Match the actual CALLS, not comment mentions of the function names: a bare
    # `orchestrator.acquire_lock()` statement and the first `apply_cli_*(orchestrator, ...)`.
    lock_m = re.search(r"\borchestrator\.acquire_lock\(\)", main_block)
    apply_m = re.search(r"\bapply_cli_\w+\(orchestrator\b", main_block)
    assert lock_m, "orchestrator.acquire_lock() not called in __main__"
    assert apply_m, "apply_cli_*(orchestrator, ...) not called in __main__"
    assert lock_m.start() < apply_m.start(), "acquire_lock() must precede the first apply_cli_* call"


# ---------------------------------------------------------------------------
# T6.5 — transactional update_symlink
# ---------------------------------------------------------------------------

class TestUpdateSymlinkTransactional:
    def _setup_links(self, mod, monkeypatch, tmp_path):
        sl_autodev = tmp_path / "sym_autodev"
        oc_root = tmp_path / "openclaw"
        oc_root.mkdir()
        monkeypatch.setattr(mod, "SYMLINK_TARGET", str(sl_autodev))
        monkeypatch.setattr(mod, "OPENCLAW_ROOT", str(oc_root))
        return sl_autodev, oc_root / "pipeline-project"

    def test_success_points_both(self, orch_inst, monkeypatch, tmp_path):
        inst, mod = orch_inst
        sl_autodev, oc_link = self._setup_links(mod, monkeypatch, tmp_path)
        new = tmp_path / "newproj"
        new.mkdir()
        assert inst.update_symlink(str(new)) is True
        assert os.path.realpath(str(sl_autodev)) == os.path.realpath(str(new))
        assert os.path.realpath(str(oc_link)) == os.path.realpath(str(new))

    def test_rolls_back_first_link_on_second_failure(self, orch_inst, monkeypatch, tmp_path):
        inst, mod = orch_inst
        sl_autodev, oc_link = self._setup_links(mod, monkeypatch, tmp_path)
        old = tmp_path / "oldproj"
        old.mkdir()
        new = tmp_path / "newproj"
        new.mkdir()
        os.symlink(str(old), str(sl_autodev))   # pre-existing -> old
        os.symlink(str(old), str(oc_link))       # pre-existing -> old

        real_replace = os.replace

        def flaky(src, dst):
            if os.path.abspath(dst) == os.path.abspath(str(oc_link)):
                raise OSError("simulated failure committing the OpenClaw-side link")
            return real_replace(src, dst)
        monkeypatch.setattr(mod.os, "replace", flaky)

        assert inst.update_symlink(str(new)) is False
        # The AUTODEV-side link must be ROLLED BACK to old (not left pointing at new).
        assert os.path.realpath(str(sl_autodev)) == os.path.realpath(str(old))
        # The OpenClaw-side link never committed.
        assert os.path.realpath(str(oc_link)) == os.path.realpath(str(old))
        # No temp/rollback artifacts left behind (scan recursively — the OpenClaw-side
        # temp is staged inside the openclaw/ subdir, not tmp_path top-level).
        leftovers = []
        for root, _dirs, files in os.walk(str(tmp_path)):
            leftovers += [os.path.join(root, f) for f in files if ".tmp." in f or ".rollback." in f]
        assert not leftovers, f"leftover temp artifacts: {leftovers}"
