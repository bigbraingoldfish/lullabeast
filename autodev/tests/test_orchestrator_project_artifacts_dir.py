"""Orchestrator per-project artifact layout: PROJECT_ARTIFACTS_DIR under symlink target."""

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PIPE = str(_REPO / "autodev" / "pipeline")
if _PIPE not in sys.path:
    sys.path.insert(0, _PIPE)


def test_project_artifacts_dir_constant():
    import orchestrator as orch

    assert hasattr(orch, "PROJECT_ARTIFACTS_DIR")
    assert orch.PROJECT_ARTIFACTS_DIR == os.path.join(
        orch.SYMLINK_TARGET, ".autodev", "pipeline"
    )


def test_module_phase_state_file_under_project_artifacts():
    import orchestrator as orch

    assert orch.PHASE_STATE_FILE == os.path.join(
        orch.PROJECT_ARTIFACTS_DIR, "phase_state.json"
    )


def test_roadmap_globs_still_use_symlink_target_not_artifacts_dir():
    text = (Path(_PIPE) / "orchestrator.py").read_text()
    assert '*[Rr]oadmap*.md")' in text or "*[Rr]oadmap*.md" in text
    assert 'os.path.join(SYMLINK_TARGET, "*[Rr]oadmap*.md")' in text or (
        "SYMLINK_TARGET" in text and "*[Rr]oadmap" in text
    )
    # Must not move roadmap discovery under PROJECT_ARTIFACTS_DIR
    bad = 'os.path.join(PROJECT_ARTIFACTS_DIR, "*[Rr]oadmap'
    assert bad not in text


def test_escalation_poll_paths_under_autodev_pipeline():
    text = (Path(_PIPE) / "orchestrator.py").read_text()
    assert 'os.path.join(root, ".autodev", "pipeline")' in text
    assert "escalation_output.done" in text and "_esc" in text


def test_missing_artifacts_instruction_uses_prefixed_paths():
    text = (Path(_PIPE) / "orchestrator.py").read_text()
    assert ".autodev/pipeline/phases/" in text
    assert ".autodev/pipeline/metrics.jsonl" in text
    assert "Write the phase archive to phases/" not in text
