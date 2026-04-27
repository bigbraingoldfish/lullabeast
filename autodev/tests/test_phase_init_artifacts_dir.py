"""phase_init writes phase_state.json and reads current_phase.json under .autodev/pipeline/."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GATE = _REPO / "autodev" / "pipeline" / "gate_scripts"
_PIPE = _REPO / "autodev" / "pipeline"
for _p in (_GATE, _PIPE):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)


def test_phase_init_writes_phase_state_under_autodev_pipeline(tmp_path, monkeypatch):
    import phase_init as phase_init_module

    project = tmp_path / "proj"
    project.mkdir()
    ad = project / ".autodev" / "pipeline"
    ad.mkdir(parents=True)
    (project / ".git").mkdir()
    def _git_ok(*_a, **_k):
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(phase_init_module.subprocess, "run", _git_ok)

    with patch.object(phase_init_module, "_derive_pipeline_project", return_value=str(project)):
        phase_init_module.init_phase()

    ps = project / ".autodev" / "pipeline" / "phase_state.json"
    assert ps.is_file(), f"expected {ps}"
    data = json.loads(ps.read_text())
    assert data.get("planner_retries") == 0


def test_phase_init_reads_current_phase_from_autodev_pipeline(tmp_path, monkeypatch):
    import phase_init as phase_init_module

    project = tmp_path / "proj"
    project.mkdir()
    ad = project / ".autodev" / "pipeline"
    ad.mkdir(parents=True)
    (project / ".git").mkdir()
    (ad / "current_phase.json").write_text(
        json.dumps({"raw_id": "CORE-1", "phase_number": 0}), encoding="utf-8"
    )

    calls = []

    def fake_run(cmd, shell, cwd, check):
        calls.append((cmd, cwd))

    monkeypatch.setattr(phase_init_module.subprocess, "run", fake_run)

    with patch.object(phase_init_module, "_derive_pipeline_project", return_value=str(project)):
        phase_init_module.init_phase()

    assert calls, "git checkout should run"
    assert "phase/CORE-1" in calls[0][0]
