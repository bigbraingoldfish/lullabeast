"""ARTIFACTS_DIR and PHASE_STATE_FILE live under .autodev/pipeline/ in the symlink project."""

import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GATE_SCRIPTS = os.path.join(_REPO_ROOT, "autodev", "pipeline", "gate_scripts")
_PIPELINE = os.path.join(_REPO_ROOT, "autodev", "pipeline")
for _p in (_GATE_SCRIPTS, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as utils_module  # noqa: E402


def test_artifacts_dir_under_autodev_pipeline():
    wd = utils_module.WORKSPACE_DIR.rstrip(os.sep)
    expected = os.path.join(wd, ".autodev", "pipeline") + os.sep
    assert utils_module.ARTIFACTS_DIR == expected


def test_phase_state_file_under_artifacts_dir():
    assert ".autodev" in utils_module.PHASE_STATE_FILE
    assert "pipeline" in utils_module.PHASE_STATE_FILE
    assert utils_module.PHASE_STATE_FILE.endswith(
        os.path.join(".autodev", "pipeline", "phase_state.json")
    )
