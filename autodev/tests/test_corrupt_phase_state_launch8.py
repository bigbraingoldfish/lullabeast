"""LAUNCH-8 — a corrupt phase_state must not silently clobber retry/reset counters.

Before LAUNCH-8, ``increment_planner_retries`` / ``increment_reviewer_retries`` /
``set_reviewer_rejected`` read phase_state inline with ``except Exception: pass``, so a
corrupt (exists-but-unparseable) file degraded to ``{}`` and the function wrote back a
single-key dict — silently wiping ``escalation_resets`` / ``nuclear_resets`` / the
executor counters. They now read via ``read_phase_state()``, which quarantines a corrupt
file and raises: the ``increment_*`` paths let that raise propagate (the main loop routes
it to escalation), and ``set_reviewer_rejected`` catches it and skips the write (its
contractual never-raise path), so no clobbering single-key write ever lands.
"""

import json
import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# sys.path is wired by autodev/tests/conftest.py — bare imports resolve.
import orchestrator as orc_module
from orchestrator import Orchestrator


@contextmanager
def _patch_paths(tmp_path):
    ps = os.path.join(str(tmp_path), "phase_state.json")
    with (
        patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)),
        patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        patch.object(orc_module, "PHASE_STATE_FILE", ps),
    ):
        yield ps


def _make_orch():
    orch = Orchestrator.__new__(Orchestrator)
    orch.lock_fd = None
    orch.state = {}
    orch.openclaw_config = {}
    orch.transition_state = MagicMock()  # avoid touching pipeline_state.json / the state machine
    return orch


def _write(path, raw):
    with open(path, "w") as f:
        f.write(raw)


def _quarantine_files(tmp_path):
    return [p for p in os.listdir(tmp_path) if "phase_state.json.corrupt" in p]


# --- corrupt read: increment_* raises (→ escalation), no clobbering write ----------

def test_increment_planner_retries_corrupt_raises_and_quarantines(tmp_path):
    with _patch_paths(tmp_path) as ps:
        _write(ps, "{ this is not valid json")
        orch = _make_orch()
        with pytest.raises(RuntimeError):
            orch.increment_planner_retries()
        assert not os.path.exists(ps), "corrupt file should be renamed away, not left/overwritten"
        assert _quarantine_files(tmp_path), "corrupt phase_state must be quarantined"
        orch.transition_state.assert_not_called()  # never reached the state transition


def test_increment_reviewer_retries_corrupt_raises(tmp_path):
    with _patch_paths(tmp_path) as ps:
        _write(ps, "not json at all")
        orch = _make_orch()
        with pytest.raises(RuntimeError):
            orch.increment_reviewer_retries()
        assert _quarantine_files(tmp_path)


# --- valid read: increment preserves every other counter ---------------------------

def test_increment_preserves_other_counters_on_valid_read(tmp_path):
    with _patch_paths(tmp_path) as ps:
        _write(ps, json.dumps({
            "planner_retries": 2,
            "reviewer_retries": 1,
            "escalation_resets": 1,
            "nuclear_resets": 1,
            "reviewer_rejected": True,
        }))
        orch = _make_orch()
        orch.increment_reviewer_retries()
        after = json.load(open(ps))
        assert after["reviewer_retries"] == 2     # incremented
        assert after["escalation_resets"] == 1    # preserved — the old bug wiped this
        assert after["nuclear_resets"] == 1       # preserved
        assert after["reviewer_rejected"] is True  # preserved
        assert after["planner_retries"] == 2      # preserved


def test_increment_absent_initializes_full_default(tmp_path):
    with _patch_paths(tmp_path) as ps:
        assert not os.path.exists(ps)
        orch = _make_orch()
        orch.increment_planner_retries()
        after = json.load(open(ps))
        assert after["planner_retries"] == 1
        # full zeroed schema present, not a single-key dict
        assert after["escalation_resets"] == 0
        assert after["nuclear_resets"] == 0
        assert after["reviewer_rejected"] is False
        assert after["executor_self_failure_retries"] == 0


# --- set_reviewer_rejected: never-raise; corrupt → skip write, don't clobber --------

def test_set_reviewer_rejected_corrupt_skips_write_never_raises(tmp_path):
    with _patch_paths(tmp_path) as ps:
        _write(ps, "}}corrupt{{")
        orch = _make_orch()
        orch.set_reviewer_rejected()  # must NOT raise (contractual never-raise)
        assert not os.path.exists(ps), "must not write a clobbering single-key file"
        assert _quarantine_files(tmp_path)


def test_set_reviewer_rejected_valid_sets_flag_and_preserves(tmp_path):
    with _patch_paths(tmp_path) as ps:
        _write(ps, json.dumps({"reviewer_retries": 3, "escalation_resets": 2}))
        orch = _make_orch()
        orch.set_reviewer_rejected()
        after = json.load(open(ps))
        assert after["reviewer_rejected"] is True
        assert after["reviewer_retries"] == 3   # preserved
        assert after["escalation_resets"] == 2  # preserved
