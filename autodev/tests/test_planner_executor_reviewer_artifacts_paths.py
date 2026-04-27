"""planner_gate, executor_gate, reviewer_gate read/write JSON under ARTIFACTS_DIR."""

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_GATE = str(_REPO / "autodev" / "pipeline" / "gate_scripts")
_PIPE = str(_REPO / "autodev" / "pipeline")
for _p in (_GATE, _PIPE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_planner_gate_default_output_under_artifacts_dir():
    text = (Path(_GATE) / "planner_gate.py").read_text()
    assert "ARTIFACTS_DIR" in text
    assert 'os.path.join(ARTIFACTS_DIR, "planner_output.json")' in text


def test_executor_gate_uses_artifacts_dir_for_io_and_workspace_for_boundary():
    text = (Path(_GATE) / "executor_gate.py").read_text()
    assert 'os.path.join(ARTIFACTS_DIR, "executor_output.json")' in text
    assert 'os.path.join(ARTIFACTS_DIR, "planner_output.json")' in text
    assert 'os.path.abspath(os.path.join(WORKSPACE_DIR, relative_path))' in text


def test_reviewer_gate_paths_use_artifacts_dir():
    text = (Path(_GATE) / "reviewer_gate.py").read_text()
    assert 'os.path.join(ARTIFACTS_DIR, "reviewer_output.json")' in text
    assert 'os.path.join(ARTIFACTS_DIR, "current_phase.json")' in text
    assert 'os.path.join(ARTIFACTS_DIR, "phases"' in text
    assert 'os.path.join(ARTIFACTS_DIR, "metrics.jsonl")' in text
