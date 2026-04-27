"""phase_resolver writes current_phase.json under <project>/.autodev/pipeline/."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GATE = _REPO / "autodev" / "pipeline" / "gate_scripts"
_PIPE = _REPO / "autodev" / "pipeline"


def test_phase_resolver_writes_current_phase_under_autodev_pipeline(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    roadmap = project / "roadmap.md"
    roadmap.write_text(
        "- [ ] `CORE-1` | LOW | Do the thing\n"
        "> exit\n",
        encoding="utf-8",
    )
    gate = _GATE / "phase_resolver.py"
    env = {**os.environ, "PYTHONPATH": f"{_GATE}:{_PIPE}"}
    r = subprocess.run(
        [sys.executable, str(gate), str(roadmap.resolve())],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(_GATE),
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = project / ".autodev" / "pipeline" / "current_phase.json"
    assert out.is_file(), f"expected {out}, stderr={r.stderr}"
    data = json.loads(out.read_text())
    assert data.get("raw_id") == "CORE-1"
